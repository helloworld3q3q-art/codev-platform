# 运行代际基础任务 3 实施报告

## 状态

DONE。已建立纯领域 `GenerationState`、追加式 transaction journal、
`ControlLease` / `ServingFence` 与稳定 `RecoveryEnvelope`；reservation 控制链、显式 journal
终态、完整 lease lineage 和第二轮职责边界回修均已通过最终终审。

本任务没有实现 Task 4 store、文件 CAS、真实 I/O、broker、launcher、systemd credential、
permit 文件或任何运行态副作用，也没有操作 WSL。

## 实施范围

- `runtime_generation_state.py`：五态不可变状态快照、严格 codec、完整状态摘要和显式状态边。
- `runtime_transaction_contract.py`：按 attempt/genesis 绑定的追加式 journal、三阶段动作、恢复决策和
  1 MiB 构造/解码闭包。
- `runtime_fencing.py`：公开 lease/fence record、不可序列化 capability proof、epoch 接管、常量时间
  token 摘要校验与严格 codec；不承载 acceptance 跨域语义。
- `runtime_generation_acceptance_validation.py`：独立承载 serving-fence 与 acceptance 的窄跨域绑定。
- `runtime_recovery_contract.py`：由冻结 attempt 和 journal 派生、只绑定稳定恢复身份的 envelope。
- 四个对应测试文件：状态边、崩溃窗口、epoch/token 围栏、strict codec、身份参与和泄漏测试。

## TDD 红绿记录

所有生产域都在实现前运行了对应测试，并观察到与当前增量一致的真实失败：

1. GenerationState
   - 首轮 `python -m pytest tests/test_runtime_generation_state.py -q` 因模块不存在收集失败。
   - codec/转换轮先分别因导出缺失失败，再补最小实现。
   - 自审补入错误对象 fail-closed 测试后出现 `2 failed`，根因是读取类型错误对象属性早于精确类型
     门禁；调整验证顺序后定向 `2 passed`。
2. Transaction journal
   - 首轮因模块不存在收集失败。
   - 历史闭包测试先得到 `DID NOT RAISE`：未提交前序动作仍能开始下一步；修复为新 key 只能跟在前一
     key 的 `COMMITTED` 之后。
   - 首动作 permit 门禁先得到 `DID NOT RAISE`；补为 journal 第一个资源动作必须是
     `serve-permit`。
   - epoch 回退测试先得到 `DID NOT RAISE`；随后在 advance、append 和解码历史三处统一拒绝降低的
     control epoch。
3. ControlLease / ServingFence
   - 首轮因模块不存在收集失败，行为轮因 `FencingContractError` 等 API 尚未导出而收集失败。
   - 最后一轮 Task 2 control acceptance 审计测试先因
     `verify_control_lease_acceptance` 不存在收集失败；最小实现后定向 `1 passed`，fencing 全文件
     `53 passed`。
4. RecoveryEnvelope
   - 首轮因模块不存在收集失败。
   - 强绑定 factory/verifier 轮因导出缺失红灯；改为从真实 `DeploymentAttempt` 与
     `TransactionJournal` 派生 controller/genesis 后全文件 `52 passed`。

最终四域聚焦命令：

```powershell
python -m pytest tests/test_runtime_generation_state.py tests/test_runtime_transaction_contract.py tests/test_runtime_fencing.py tests/test_runtime_recovery_contract.py -q
```

结果：`226 passed`。没有用 skip、xfail、伪造 store 或实际副作用掩盖失败。

## GenerationState 契约

状态转换与维护门禁如下：

| 转换 | 前态 | 后态 | `maintenance_active` | serving 身份 |
|---|---|---|---:|---|
| `begin_switch` | 公开 `steady` | `switching` | `True` | 保留旧 serving/fence |
| `begin_validation` | `switching` | `validating` | `True` | 保留旧 serving/fence |
| `commit_serving` | `validating` | 提交态 A：`steady` | `True` | 切到目标 generation/新 fence |
| `prepare_serving_publication` | 提交态 A | 公开态 B：`steady` | `False` | 身份不变，清除瞬态控制绑定 |
| `mark_restricted` | 非终态 | `restricted` | `True` | serving 关闭 |
| `mark_safety_unproven` | 非终态 | `safety_unproven` | `True` | serving 关闭且不可继续转换 |

- `begin_switch`、`begin_validation`、`commit_serving` 均需要精确 proof/record 与 attempt/state 绑定。
- A→B 是确定性纯转换；staged permit 可预绑定 B 的完整摘要。A 仍处于 maintenance，permit 与 A 不匹配，
  因而 writer 保持关闭；CAS 成功到 B 后才可开放。
- 没有通用 `replace_state(**kwargs)` 或任意字段替换入口。恢复只允许同一 attempt 使用不低于当前
  状态审计值的 lease epoch；旧 epoch 被拒绝。
- 使用独立 `mark_restricted`，避免把“可审计限制态”和“安全性无法证明的终态”混成一条状态边。

## Transaction journal 与四个崩溃窗口

动作只允许 `PREPARED → APPLIED → COMMITTED`，每次状态变化追加新记录。相同 key/状态的重复追加
幂等；身份漂移、跳级、回退、乱序、跨 attempt、epoch 回退和未提交前序动作均失败关闭。

| 持久窗口 | journal 可见头 | 恢复决策 | 约束 |
|---|---|---|---|
| PREPARED 已 fsync，资源未变 | `PREPARED` | `RECONCILE` | 先读外部事实，不直接重放 |
| 资源已变，APPLIED 未 fsync | `PREPARED` | `RECONCILE` | 同样先复证，避免重复非幂等副作用 |
| APPLIED 已 fsync，COMMITTED 未 fsync | `APPLIED` | `COMMIT` | 只推进 journal 状态 |
| COMMITTED 已 fsync，下一步未开始 | `COMMITTED` | `ADVANCE` | 进入下一序号 |

`TransactionRecoveryDirective` 故意没有 `REPLAY`。对于 `PREPARED` 无法仅凭 journal 区分前两个
窗口，因此统一返回更保守的 `RECONCILE`；未来 adapter 必须先读取并核对资源事实，再决定记录
APPLIED 或执行补偿。journal 的第一个 resource 固定为 `serve-permit`，其具体“撤销”意图由
`operation_sha256` 绑定；纯领域契约不解释或执行该操作。

## Lease、fence 与 writer

- `ControlLeaseRecord` / `ServingFenceRecord` 只保存 token SHA-256；原始 32-byte capability 只存在于
  `ControlLeaseProof` / `ServingFenceProof`，且 `repr=False`。
- control recovery 只接受同一活动 attempt、严格更大的 epoch，并强制 token 轮换。lease 接管本身
  不改变 `GenerationState`，因此不会错误关闭现有线上服务。
- serving fence 在一次提交内稳定。writer 仅在公开 `steady`、maintenance 已关闭、当前 generation、
  当前 fence 以及 token proof 全部一致时授权；旧 writer 在 `begin_switch` 后立即失效，目标 writer
  在 B 发布前也始终关闭。
- control/serving token 摘要比较均使用 `hmac.compare_digest`。公开 record 与 Task 2
  `GenerationAcceptance` 分别复验 control 三字段审计关系和 serving 五字段绑定关系。
- proof 没有 encoder/decoder，不进入 canonical identity；canonical JSON、公开 payload、异常文本、
  `repr` 与日志测试均证明原始 token 不泄漏。普通 bytes 也不能冒充 capability。

## RecoveryEnvelope

- factory 只接受真实冻结 `DeploymentAttempt` 与同 attempt 的 `TransactionJournal`；
  `controller_tree_sha256` 从 attempt 派生，`journal_genesis_sha256` 从 journal 派生，读后 verifier
  再复证二者关系。
- 路径只接受规范 POSIX 相对路径，interpreter 必须严格位于 controller root 下；拒绝绝对路径、
  Windows drive、反斜杠、`.` / `..`、重复分隔、`current` 段、控制字符、Unicode/shell/env 注入和
  超长段/路径。
- envelope 绑定固定 controller、interpreter、transaction store 与 journal genesis；不包含模块名、
  argv、环境覆盖、会变化的 journal head 或 control lease epoch。launcher 必须从各自 CAS store
  读取并复验当前 head/lease，本任务不实现 launcher。

## Identity 字段参与表

| 摘要/身份 | 参与字段 | 明确排除 |
|---|---|---|
| `GenerationState` SHA | 14 个持久字段全部参与 | 无隐式排除 |
| action key | attempt ID、step sequence、resource kind、resource ID | 状态、时间、前后/操作摘要、lease 审计 |
| journal genesis | schema、attempt ID、created_at | actions、updated_at、head |
| journal head SHA | 完整 journal（含全部动作） | 无隐式排除 |
| control/serving token SHA | 原始 32-byte token | proof 对象其余字段 |
| public control identity | attempt、epoch、token 摘要；record 另含 owner/status/终态审计 | 原始 token |
| public serving identity | fence ID、generation、accepted attempt、epoch、token 摘要 | 原始 token |
| `RecoveryEnvelope` SHA | 9 个持久字段全部参与 | journal head、lease epoch、模块、argv、环境 |

## 最终验证

```text
四域聚焦：226 passed
Task 2/3 十文件直接消费者：580 passed
全部 tests/test_runtime*.py：1471 passed, 160 skipped
Ruff：All checks passed!；8 files already formatted
compileall：成功，无输出
```

160 个 skip 与任务前基线一致；四个新测试文件没有 skip/xfail。

生产/测试文件行数分别为：state `465/487`、journal `553/358`、fencing `504/565`、recovery
`233/251`，全部低于 600 行。静态扫描确认四个生产领域模块不依赖 `ops`、`web`、`os` 或
`subprocess`；没有 proof codec、原始 token 序列化、`object.__setattr__` 或任意状态替换旁路。
`git diff --check` 在提交前最终复验。

## 提交与门禁

本报告随单一提交 `feat(runtime): 增加代际状态事务日志与围栏` 一同提交，author/committer 固定为
`helloworld3q3q <helloworld3q3q>`。只允许仓库 hook 推送 `origin/dev`；不显式 push、不推
`origin`、不操作 WSL、不等待 reindex。

---

## 第一轮复审修复（2026-07-20）

### 状态与范围

DONE。复审确认的五类领域缺口已经封闭：state/journal 写入口使用活动
`ControlLeaseRecord + ControlLeaseProof` 双因子授权；journal 固化 epoch/token 相邻映射；首动作改为
类型化撤销当前 permit；RecoveryEnvelope 拒绝 runtime root 本身；1 MiB 构造域获得真实合法边界回归。

本轮只修改纯领域契约、测试与本报告。`runtime_fencing.py` 的既有 verifier 经复证正确，生产文件没有
改动。没有新增 Task 4 store/CAS、文件 I/O、adapter、permit 文件、systemd、subprocess、ops/web
依赖或第二套摘要算法，也没有主动操作 WSL、索引、数据库或新建 worktree。

### 根因复证

1. state 五个入口和 journal 三个入口原先只接收公开可构造的 proof；attempt/epoch 自比较无法证明
   token 已签发且 lease 仍为 ACTIVE。
2. journal 的 live 与完整历史都只拒绝 epoch 倒退；同 epoch 换 token、epoch 提升复用 token 都能
   通过。
3. journal 首动作只看 `resource_kind`，`operation_sha256` 是通用摘要，不能单独证明“撤销当前活动
   permit”的类型和稳定资源身份。
4. `PurePosixPath(".").parts == ()`，原逐段循环没有执行，导致整个 runtime root 被接受为 controller
   tree。
5. `TransactionJournal.__post_init__()` 已有 1 MiB 聚合门禁，但测试只覆盖 decoder 原始 oversize，
   没有证明合法对象域的构造/encode/decode 闭包。

### TDD RED/GREEN 证据

证据口径更正（第二轮复审）：Fix1 把 wrong-token 场景与新签名接口测试一同加入；首个
`6 failed, 66 deselected` 全部是旧 API 的 `TypeError`，只证明 `record + proof` 授权接口尚不存在，
不能作为“旧实现接受错误 token”的行为 RED。原漏洞的行为复现应引用第一次复审已在 pre-fix 基线上
执行的窄探针：使用同 attempt/epoch、但 token 不同的 proof 调用旧 proof-only 写入口时被放行。当前
wrong-token 回归用例全部 GREEN，作为修复后的防回归证据；本报告不为该场景补写未实际观察到的
`DID NOT RAISE` 历史。

#### 1. state 活动 lease 双因子

- 接口 RED：新增真实签发 record/proof、同 attempt/epoch 错 token、RETIRED record、错误 proof 类型及
  真实 recovery epoch 用例后，定向命令得到 `6 failed, 66 deselected`；五个入口均因旧签名只接收
  proof 而报 `TypeError`。该结果只证明接口缺口，不单独证明 wrong-token 行为漏洞。
- GREEN：五个入口显式加入 record，在内部统一调用 `verify_control_lease()` 并把 fencing 异常转换为
  `GenerationStateError`；持久 epoch 从已验证 record 取得。定向 `6 passed`，当前完整回归中的错误
  token 用例继续 GREEN。
- 消费者迁移前完整 state 得到 `23 failed, 49 passed`，全部是旧调用缺少 record；迁移为真实签发/
  recovery 后最终 `71 passed`。fencing 真实时序集成为 `53 passed`。

`prepare_serving_publication()` 仍是不接收 lease 的确定性纯转换；Task 4 的 A→B CAS 授权没有进入
本轮。

#### 2. journal 双因子与 epoch/token 映射

- 双因子 RED：期望 API 首轮在模块收集时因 `prepare_transaction_action()` 旧签名报 `TypeError`；补
  production 后又由旧 `append_transaction_action()` 调用报缺失 record。逐一迁移后，三个入口错误
  token/RETIRED/错误 proof 与真实 recovery 审计定向为 `4 passed`。
- 映射 RED：live advance、live append、直接构造、decoder 分别覆盖“同 epoch 换 token”和“epoch
  提升复用 token”，得到 `8 failed, 57 deselected`，全部为 `DID NOT RAISE`。
- 映射 GREEN：新增唯一 `_require_control_successor()`；`advance_transaction_action()`、
  `append_transaction_action()` 与 `TransactionJournal.__post_init__()` 完整历史复验共用该语义，定向
  `8 passed`。
- 审计字段由已验证 record 的 epoch/token digest 派生；append 再把 action 审计与 record 以常量时间
  摘要比较绑定。更高 epoch 正例只由 `recover_control_lease()` 取得并轮换 token。

#### 3. 类型化撤销当前 permit

- RED：测试先因缺少 `CURRENT_SERVE_PERMIT_RESOURCE_ID` / `TransactionActionIntent` 导出而收集失败。
- 首次 GREEN 尝试得到 `1 failed, 5 passed`：`advance_transaction_action()` 构造下一状态时遗漏传播
  intent。补入该持久字段后定向 `6 passed`，transaction 主文件最终 `69 passed`。
- `TransactionActionIntent` 仅有 `REVOKE_CURRENT_SERVE_PERMIT` 与 `APPLY_RESOURCE`；首动作必须同时
  满足 revoke intent、`serve-permit` kind、`current-serving-permit` ID 和 sequence 1。
- intent 进入 dataclass、严格字段集合、canonical action identity、codec 和同键身份漂移比较；action
  key 仍只由 attempt、sequence、resource kind、resource ID 计算。普通资源动作可在首动作 COMMITTED
  后追加。

本节更正并取代原报告“撤销意图只由 `operation_sha256` 绑定”的表述：该摘要仍绑定具体操作内容，
但类型语义和当前 permit 稳定身份现在由显式 intent/kind/resource ID 三元组证明。

#### 4. RecoveryEnvelope 根路径

- RED：真实 factory 与修改规范 payload 后的 decoder 各自接受 `controller_root_relative="."`，得到
  `2 failed, 52 deselected`，均为 `DID NOT RAISE`。
- GREEN：`_require_runtime_relative()` 显式拒绝空 `posix.parts`；定向 `2 passed`，recovery 全文件
  `54 passed`。

#### 5. 1 MiB 有效构造闭包

- 新增专责 `tests/test_runtime_transaction_size_contract.py`，避免 599 行 journal 主测试继续膨胀；它
  不含生产 helper 或私有上限 monkeypatch。
- 测试用真实 attempt、真实签发 lease、1366 个连续资源步骤生成最多 4096 条合法
  PREPARED/APPLIED/COMMITTED 记录，再按规范 mapping 二分出 1 MiB 最大合法前缀。
- 现有门禁首次运行即 `1 passed`，符合复审 brief“实现已有、缺直接回归”的根因。为验证测试敏感性，
  临时移除 `__post_init__()` 的 size 调用后得到精确 mutation RED：`1 failed`，失败点为超限合法
  journal `DID NOT RAISE`；立即原样恢复调用后 `1 passed`。
- 最大可接受前缀可由正式 `TransactionJournal` 构造并 encode/decode 往返；再加一条历史合法记录后
  canonical JSON 超过 `1_048_576` bytes，构造阶段拒绝。没有 `object.__setattr__`、伪 store、不可能
  DTO 或私有上限替换。

### 五项门禁自审

1. **活动 lease**：state 5 个、journal 3 个控制写入口均在自身边界调用同一个
   `verify_control_lease(record, proof)`；错误统一收敛为各自领域异常。state 复验 attempt 与相对当前
   epoch，journal 审计取自 record。测试逐入口覆盖错 token、RETIRED 与错误 proof。
2. **历史映射**：相同 epoch 只允许相同 token digest；epoch 提升必须不同 digest；倒退始终拒绝。
   live advance、live append、直接构造与 decoder 均调用同一私有不变量，没有双真值源。
3. **首动作**：唯一稳定常量为 `CURRENT_SERVE_PERMIT_RESOURCE_ID = "current-serving-permit"`；intent
   参与持久 identity/codec 但不改变既定 action key 投影。错误 intent/kind/ID 均有回归。
4. **根路径**：factory、普通 decoder 与 context decoder 最终都经过同一 `_require_runtime_relative()`，
   精确 `"."` 无法进入对象域。
5. **大小闭包**：构造和 decoder 共用生产代码唯一的 1 MiB 真值；测试 literal 只表达外部契约边界，
   不修改或读取私有常量。边界相邻两侧均由合法 action/history 组成。

### 最终验证

```text
五域聚焦（含 size 专责文件）：248 passed
Task 2/3 直接消费者：423 passed
全部 tests/test_runtime*.py：1493 passed, 160 skipped
Ruff：All checks passed!
Ruff format：8 files already formatted
compileall -q codev_platform：成功，无输出
git diff --check：成功，无输出
```

复审 brief 中的 `tests/test_runtime_acceptance_contract.py` 在仓库不存在；直接消费者验证使用真实文件
`tests/test_runtime_generation_acceptance.py`，其余指定文件保持不变，并额外显式纳入 size 专责文件。
160 个 skip 与本任务前基线一致；本轮修改/新增测试没有 skip/xfail。

格式化后的生产文件行数为 state `497`、journal `598`、recovery `234`；测试为 state `598`、journal
`599`、size `133`、fencing `568`、recovery `276`，全部严格低于 600 行。静态扫描确认：

- proof 没有 codec、canonical 序列化入口或原始 token 泄漏；
- 生产领域模块没有 store/I/O/systemd/subprocess/ops/web 反向依赖；
- 测试没有本轮 skip/xfail、`object.__setattr__`、私有上限 monkeypatch 或伪造 store；
- `runtime_fencing.py` 生产 diff 为空；所有新 verifier 使用均复用现有实现。

### 本轮提交门禁

本轮使用单一修复提交 `fix(runtime): 绑定控制租约并封闭事务入口`，author/committer 固定为
`helloworld3q3q <helloworld3q3q>`。只允许仓库 hook 推送 `origin/dev`；不显式 push、不推
`origin`、不主动操作 WSL、不等待或手工触发 reindex。

---

## 第二轮复审修复（2026-07-20）

### 状态与范围

DONE。只修复 `append_transaction_action()` 在 control lease 接管后无法精确幂等重试已落盘旧动作的
门禁顺序，并新增专责 takeover/idempotency 回归。本轮没有修改 prepare/advance 的 lease 验证、journal
历史不变量或 fencing verifier；没有引入 Task 4 store、I/O、adapter、WSL 运行态或 worktree。

### 根因与门禁顺序

旧实现先调用 `_require_action_lease()`，同时验证当前调用 capability 与 action 历史审计，再查找同 key
动作。epoch 4 动作已经落盘、epoch 5/new token 恢复器精确重试时，合法的新 record/proof 会先与旧
action 审计发生 epoch 不一致，永远到不了幂等返回。

修复后 append 只有一条顺序明确的路径：

1. 精确类型检查后，以当前 `ControlLeaseRecord + ControlLeaseProof` 对 `current.attempt_id` 做 ACTIVE
   授权；wrong token、RETIRED record 与错误 proof 类型均早于任何 no-op 失败。
2. action 必须属于 journal attempt；随后查找同 key 最新动作并先拒绝 identity 漂移。
3. identity 与 state 相同表示不产生写入或副作用，直接返回原 journal 对象；旧 action 审计保持不变。
4. state 不同或 action 不存在表示将真实追加，此时 `_require_action_audit()` 强制 action epoch/token
   精确绑定已验证的当前 record，然后继续执行 control successor、状态边、首动作与步骤连续门禁。

`_require_action_lease()` 被拆成调用授权与 action 审计两个单一职责阶段，但 verifier 只调用一次；
prepare/advance 仍在自身入口完整验证当前 lease，live/history 的 epoch/token 映射约束没有放松。

### TDD RED/GREEN 证据

- RED：先新增 `tests/test_runtime_transaction_takeover_contract.py`，执行
  `python -m pytest tests/test_runtime_transaction_takeover_contract.py -q -vv` 得到
  `2 failed, 5 passed`。PREPARED 与 APPLIED 两个参数化失败都精确落在旧
  `_require_action_lease()` 的 `ControlLeaseRecord epoch 与动作审计不一致`，其余安全门禁先保持 GREEN。
- GREEN：最小重排 append 门禁并拆出 `_require_action_audit()` 后，同一文件 `7 passed`；PREPARED/
  APPLIED 旧动作均由新 ACTIVE lease 授权后返回同一 journal 对象且复用原 history tuple。
- 防回归：同一已存在动作搭配 wrong-token proof 或 RETIRED record 均在 no-op 前失败；接管后真实
  PREPARED→APPLIED 使用 epoch 5/new token 审计追加；空 journal 与不存在 action 均拒绝 old-audit/
  new-lease 组合。

### Fresh 验证与自审

```text
takeover 专责：7 passed
transaction 主文件 + takeover + size：77 passed
六组焦点（state/transaction/takeover/size/fencing/recovery）：255 passed
显式枚举 86 个 tests/test_runtime*.py：1500 passed, 160 skipped
Ruff：All checks passed!
Ruff format：4 files already formatted
compileall -q codev_platform：成功，无输出
```

生产 `runtime_transaction_contract.py` 为 `598` 行；主 transaction 测试 `599` 行，新 takeover 测试
`217` 行，size 测试 `133` 行，全部严格低于 600。生产 diff 只移动 append 门禁并拆分私有 helper；
没有新增摘要真值、重复 verifier、skip/xfail、mock、store 或外部副作用。`git diff --check` 在提交前
最终执行并记录。

### 本轮提交门禁

本轮使用单一修复提交 `fix(runtime): 保持租约接管后的日志幂等`，author/committer 固定为
`helloworld3q3q <helloworld3q3q>`。只允许仓库 hook 推送 `origin/dev`；禁止推 `origin`，不操作 WSL
运行态，不手工触发或等待 reindex。

---

## 第三轮复审修复（2026-07-20）

### 状态与范围

DONE。只封闭 Fix2 的 latest-only 遗留：control lease 接管后，精确重试同 key 完整历史中较早的
PREPARED/APPLIED 也保持幂等。本轮只修改 transaction append 的历史查找、既有 takeover 专责测试和
本报告；没有引入 store、I/O、adapter、registry、WSL 运行态或新领域模块。

### 根因与完整历史语义

Fix2 已把当前 ACTIVE lease 授权放到 no-op 前，但 `_latest_for_key()` 只返回同 key 最新记录。journal
已经持久化 PREPARED→APPLIED 时，迟到重试 PREPARED 会看到 latest=APPLIED，无法命中同 state，继而
进入 action audit 并因 old epoch/token 与接管后的新 record 不一致而失败。

修复以 `_history_for_key()` 等量替换 latest-only helper：一次取得有序同 key 完整历史，最后一项仍是
identity drift 与合法下一状态的唯一 latest 真值；完整 tuple 仅用于判断候选 state 是否已经持久化。
append 门禁顺序保持为：

1. 当前 record/proof 先对 journal attempt 做 ACTIVE 授权；wrong token、RETIRED 和错误类型早于 no-op
   失败。
2. action attempt 必须匹配；候选 identity 必须与同 key latest identity 相同。
3. 完整同 key 历史存在同 state 时返回原 frozen journal，不写入候选旧审计，也不改变 history。
4. 历史不存在该 state 时才校验 action 审计与当前 lease，并继续执行 global control successor、状态边、
   新动作顺序及完整 journal 构造校验。

### TDD RED/GREEN 证据

- RED：先扩展 `tests/test_runtime_transaction_takeover_contract.py`，执行专责文件得到
  `3 failed, 12 passed`。三项失败分别为 APPLIED 后重试旧 PREPARED、COMMITTED 后重试旧 PREPARED
  与旧 APPLIED，全部精确落在 `_require_action_audit()` 的 old action/new record epoch 不一致；历史
  no-op 的 wrong-token/RETIRED、identity drift、不存在 state 与真实后续追加均保持 GREEN。
- GREEN：仅替换同 key 查找并将 no-op 条件改为完整历史 state_seen 后，专责文件 `15 passed`。
- 组合时序：APPLIED 精确 no-op 后，以接管 lease 正式生成并追加 COMMITTED；新记录审计为 epoch 5/
  new token，既有 PREPARED/APPLIED tuple 与全部旧审计保持不变。COMMITTED 历史下的 PREPARED 与
  APPLIED 重试都返回同一 journal/history 对象。

### Fresh 验证与自审

```text
takeover 专责：15 passed
transaction 主文件 + takeover + size：85 passed
六组焦点（state/transaction/takeover/size/fencing/recovery）：263 passed
显式枚举 86 个 tests/test_runtime*.py：1508 passed, 160 skipped
Ruff：All checks passed!
Ruff format：2 files already formatted
compileall -q codev_platform：成功，无输出
git diff --check：成功，无输出
```

格式化后的生产 `runtime_transaction_contract.py` 为 `596` 行，takeover 专责测试为 `335` 行，均严格
低于 600。生产改动没有重复 verifier 或摘要/状态真值；latest 仍来自同一 ordered history 的最后一项。
测试使用真实 issue/recover/retire factory，没有 mock、monkeypatch、skip/xfail、伪 store 或外部副作用。

### 本轮提交门禁

本轮使用单一修复提交 `fix(runtime): 完整支持接管后的历史幂等`，author/committer 固定为
`helloworld3q3q <helloworld3q3q>`。只允许仓库 hook 推送 `origin/dev`；禁止推 `origin`，不操作 WSL
运行态，不手工触发或等待 reindex。

---

## 第四轮测试终审修复（2026-07-20）

### 状态与证据修正

DONE。生产实现已经通过安全/架构终审，本轮只封闭 takeover 测试的一个假绿窗口。原
`test_applied历史幂等后可用接管lease继续提交()` 仅比较 `completed.actions[:2]` 与旧历史，再检查单独
构造的 `committed`；若最终 append 错误返回原 journal，这些断言仍可能全部通过。因此第三轮报告中
“COMMITTED 已正式追加”的结论此前证据不足。

测试现直接断言：

- `completed.actions == (*original_history, committed)` 且最终长度精确为 3；
- 尾记录 state 为 COMMITTED；
- 尾记录 epoch/token 审计均来自 takeover 后 record；
- 原 PREPARED/APPLIED history 与旧 epoch 审计保持不变。

这是测试证据强化，不是生产行为修复；增强断言在已批准的现实现上首次执行即 `15 passed`，没有虚构
RED。上述直接断言现在足以证明最终 COMMITTED 确实进入 `completed.actions`，而非只存在于局部变量。

### Fresh 验证与范围自审

```text
takeover 专责：15 passed
transaction 主文件 + takeover + size：85 passed
六组焦点（state/transaction/takeover/size/fencing/recovery）：263 passed
显式枚举 86 个 tests/test_runtime*.py：1508 passed, 160 skipped
Ruff：All checks passed!
Ruff format：1 file already formatted
git diff --check：成功，无输出
```

takeover 专责测试为 `337` 行，严格低于 600；`git diff --name-only -- codev_platform` 为空。没有新增
helper、模块、mock、skip/xfail、store 或外部副作用。

### 本轮提交门禁

本轮使用单一测试提交 `test(runtime): 固化接管后的最终提交`，author/committer 固定为
`helloworld3q3q <helloworld3q3q>`。只允许仓库 hook 推送 `origin/dev`；禁止推 `origin`，不操作 WSL
运行态，不手工触发或等待 reindex。

---

## 第五轮职责边界与文件纪律终审（2026-07-20）

### 完成结论

DONE。第二轮终审的四项剩余约束均已关闭：

1. serving-fence 与 acceptance 的跨域验证从 `runtime_fencing.py` 移至独立 validation 模块，
   fencing 仅保留本域模型与 capability 校验。
2. state 与 terminal 的公开入口显式拒绝 `None` lineage，不再回退为 singleton。
3. state、fencing、terminal 测试按单一职责拆为模型/转换/lineage、lease/fence/writer、
   完成/证据/退租文件，共享工厂不重复，所有相关文件均小于 600 行。
4. terminal validation 的两处底层异常映射在终审中发现对调；已先用公开入口复现
   `FencingContractError` 与 `GenerationAcceptanceBindingError` 泄漏，再改为统一抛出
   `TransactionTerminalError` 并复验。

### 最终验证

```text
第二轮定向回归：240 passed
全部 tests/test_runtime*.py：1619 passed, 162 skipped
Ruff：All checks passed!
Ruff format：相关文件均已格式化
```

架构、状态/fencing、terminal/recovery 三角色只读终审均为无 Critical、无 Important。AST 调用核对确认
公开 state/terminal 控制入口均显式传入 `control_lease_lineage`；`runtime_fencing.py` 不再出现
acceptance validation 的反向依赖。`git diff --check` 在本轮最终文档更新后重新执行。

本轮未执行暂存、提交、推送、WSL 操作或任何运行态副作用。Task 4 及后续 store、adapter、集成/WSL
验收仍未开始，不能据此宣称部署环境已可用。
