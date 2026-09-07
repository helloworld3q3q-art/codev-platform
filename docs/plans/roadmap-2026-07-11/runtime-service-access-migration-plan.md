# 运行时服务访问授权与旧对象旁路迁移实施计划

> **状态（2026-07-20）：** Task 1、2、3A、3B 已完成；3C 叶子能力已落地，按新代际契约的接线
> 由 Foundation Task 5 继续。旧 Task 4 已被“每个 generation 独立 base”设计取代，Task 5/6
> 的最终验证与清理由 Integration 9–13 承接。本计划不再建立独立执行队列。
>
> 本计划按 TDD 逐项执行。每项实现后先做独立审查；正式部署前必须完成本地回归、
> WSL root 实物权限验证和真实服务 UID 导入验证。

## 一、问题与目标

正式安装入口保留 `umask 077`，因此旧版 base/release 的目录和大量普通文件分别落成
`0700`、`0600`。这些对象满足“root 所有且非 root 不可写”的执行信任，却不满足
systemd `User=helloworld` 所需的读、遍历和执行权限。真实降权探针已经证明：root 可以从
目标 release 导入 `codev_platform`，UID/GID 1000 运行同一解释器时却退回系统前缀并报
`ModuleNotFoundError`。

目标是在不放宽控制面、不修改旧对象身份的前提下，建立唯一的运行时访问策略：

- base/release 始终由 root 所有，服务主组只有读、遍历和执行权限，永远没有写权限；
- `locks`、`deployments`、隔离区、日志和配置暂存等控制面继续保持 root 私有；
- 模式在内容清单和元数据封存前定型，内容对象发布只改变 GID；受管父链由独立接口
  精确收敛 GID 和遍历 mode，不递归修改任何内容对象或私有兄弟目录；
- 旧 schema 2 对象不原地 `chmod`，通过新访问策略身份旁路重建 base、baseline 和 target；
- target 与 rollback baseline 都在维护停机前完成真实降权导入证明；
- 任一授权、复验或探针失败都不得停止现有服务、迁移数据库或切换 target。

## 二、架构边界

采用六个单一职责模块，并由现有部署制品适配器轻量聚合：

1. `core.runtime_models` 维护访问策略版本、BaseMetadata schema 和内容身份。
2. `runtime_fd_tree` 负责 fd-relative、no-follow、同设备、身份稳定和预算等唯一遍历机制；
   只接受封闭操作类型，不暴露原始 fd 或任意高权限回调。
3. `runtime_object_access` 负责 root 对象树的规范 mode 定型与只读复验，不解析用户账号。
4. `runtime_service_access` 分别负责受管父链精确收敛，以及把已封存内容对象发布给唯一服务主组；
   前者只处理三个白名单目录，后者只改内容对象 GID。
5. `runtime_service_process` 维护服务账号校验、环境编码和唯一 setpriv argv 构造契约。
6. `runtime_target_user_probe` 负责通过固定 `setpriv + env -i` 真正降权并验证目标导入来源。

现有 `runtime_deployment_artifacts` 只编排构建、授权、双 release 探针和回执证据。

访问策略名称固定为 `root-service-group-read-v1`。对象目录规范 mode 为 `0750`；普通文件
按原 owner-executable 位映射为 `0750` 或 `0640`；符号链接不跟随，必须 root 所有。
模式规范化后禁止组写和其他用户权限。内容对象发布只把规范对象的 GID 从 root 或同一目标组
切换为目标服务主组，不得接管属于其他非 root 组的对象。`runtime`、`bases`、`releases`
三个受管父目录不属于内容身份，可从受信的 `{0700, 0710, 0750, 0755}` 精确收敛为
`root:<服务主组> 0710`；迁移前 uid 必须为 root，gid 只能是 root 或同一目标服务 GID；
其他路径、mode、owner 或非 root GID 一律在首次变更前拒绝。

## 三、全局安全约束

- 保留安装脚本的全局 `umask 077`，不得改成 `022`。
- 所有目录遍历都不得跟随符号链接；拒绝特殊文件、跨设备挂载、普通文件硬链接和预算超限。
- 拒绝对象树和父链上的扩展 ACL/default ACL；不得让 ACL 绕过纯 mode 与目标主组证明。
- 对象 mode 必须在 purelib inventory、base.json 和 release.json 最终复验前定型。
- `purelib_inventory_sha256`、base metadata 摘要和 release ID 必须在封存后保持稳定。
- BaseMetadata 升级为 schema 3 并显式记录访问策略；base ID 必须包含访问策略版本。
- schema 2 对象只保留审计和旧服务回退，不原地修改、不伪装成 schema 3。
- 新 baseline 必须与 target 使用同一新版 base；旧 current 不满足新身份时，旁路构建同提交 baseline。
- 只允许原地收敛 `runtime`、`bases`、`releases` 三个非内容父目录；旧 schema 2 的
  `bases/<id>`、`releases/<id>` 仍禁止原地 chmod/chgrp。
- 对受管父链只开放目标组遍历（`0710`）；`locks`、`deployments`、`candidates`、隔离区等
  私有兄弟不得枚举、递归授权或修改。
- 降权命令固定绝对路径，清空补充组和环境，启用 no-new-privs，并清空 bounding、
  inheritable 和 ambient capability 集合。
- 输出和回执不得包含 token、配置内容、环境变量或底层异常正文。
- 只推送 `origin/dev`，禁止访问或推送 GitHub。

---

### 任务一：把访问策略纳入 base 身份契约

**文件：**

- 修改：`codev_platform/core/runtime_models.py`
- 修改：`tests/test_runtime_models.py`
- 修改：所有测试中的 `BaseMetadata` 固定构造器

**TDD：**

1. 先写测试证明 schema 2 被新控制器拒绝、schema 3 必须携带精确访问策略。
2. 先写测试证明 base ID 的规范载荷包含访问策略，旧/新策略不能得到同一 ID。
3. 增加 `RUNTIME_ACCESS_PROFILE = "root-service-group-read-v1"`，BaseMetadata 增加
   `access_profile`，schema 固定为 3。
4. `compute_base_id()` 把精确策略放入规范 JSON；不得允许调用方伪造任意策略。
5. 更新少量固定模型构造器，不引入默认值掩盖旧元数据。

**验证：**

```powershell
python -m pytest tests/test_runtime_models.py tests/test_runtime_metadata_io.py -q
python -m ruff check codev_platform/core/runtime_models.py tests/test_runtime_models.py
```

### 任务二：在封存前定型并复验对象 mode

**文件：**

- 新建：`codev_platform/runtime_object_access.py`
- 修改：`codev_platform/runtime_base.py`
- 修改：`codev_platform/runtime_build.py`
- 修改：`codev_platform/runtime_release_environment.py`（仅在职责边界需要时）
- 新建：`tests/test_runtime_object_access.py`
- 修改：`tests/test_runtime_base.py`
- 修改：`tests/test_runtime_build.py`

**TDD：**

1. 覆盖 `0700/0600/0711/0755/0644` 输入到目录 `0750`、普通文件 `0640/0750` 的确定映射；
   仅 group/other executable 的 `0610/0601` 必须收敛为 `0640`，不得提升 owner 执行权限。
2. 覆盖符号链接不跟随、root owner、普通文件单链接、同设备、文件数/字节预算和特殊文件拒绝。
3. 覆盖幂等定型、定型后静态复验、任意 group-write/other 位或 mode 漂移失败关闭。
4. base 在首次 inventory 前定型 purelib mode，在 base.json 落盘后再次覆盖新元数据并复验；
   复用 base 必须验证访问策略和对象 mode。
5. release 在应用安装与动态探针完成后定型，在 release.json 落盘后再次覆盖新元数据并复验；
   复用 release 必须验证对象 mode。
6. 证明定型后的 inventory 摘要、base.json 摘要和 release ID 经重复复验保持不变。

**验证：**

```powershell
python -m pytest tests/test_runtime_object_access.py tests/test_runtime_base.py tests/test_runtime_build.py -q
python -m ruff check codev_platform/runtime_object_access.py codev_platform/runtime_base.py codev_platform/runtime_build.py
```

### 任务三：抽取安全遍历内核，建立服务主组发布器与真实降权探针

**文件：**

- 新建：`codev_platform/runtime_fd_tree.py`
- 新建：`codev_platform/runtime_service_access.py`
- 新建：`codev_platform/runtime_target_user_probe.py`
- 新建：`codev_platform/runtime_service_process.py`
- 新建：`tests/test_runtime_fd_tree.py`
- 新建：`tests/test_runtime_service_access.py`
- 新建：`tests/test_runtime_target_user_probe.py`
- 新建：`tests/test_runtime_service_process.py`
- 修改：`codev_platform/runtime_object_access.py`
- 修改：`codev_platform/runtime_deployment_artifacts.py`（仅把既有服务账号与 setpriv 构造委托给共享模块）
- 修改：对应既有测试

**TDD：**

1. 任务 3A 先把 Task 2 的 fd-relative、no-follow、同设备、身份稳定、特殊文件/硬链接、
   ACL/xattr 与预算机制等价抽到共享 walker；API 只接受封闭的 verify/mode/GID 操作，
   不暴露原始 fd、DirEntry、绝对路径或任意回调。抽取后先复跑并复审 Task 2，行为不得漂移。
   verify 不允许任何身份字段变化；mode 只允许权限位与 ctime 变化；GID 只允许 gid 与 ctime
   变化；符号链接在三种操作下均保持只读。
2. 任务 3B 的发布器只接受 Linux root、规范绝对 runtime 根、非 root 服务 UID/GID 和完整对象 ID。
3. namespace 接口先完整预检，再且仅把 `runtime`、`bases`、`releases` 从合法迁移状态收敛为
   `root:<服务主组> 0710`；先 chgrp、再 chmod、再 fsync/复验，私有兄弟元数据必须逐项保持不变。
4. 内容对象只处理调用方精确列出的 schema 3 base/release；按排序后的 release 排他锁、再按
   base 排他锁持有完整预检、叶子优先 GID 发布、持久化和复验全过程。
5. 对目录和普通文件只执行 no-follow GID 发布；符号链接不跟随也不改组，只证明 root owner 和
   稳定目标。mode 不规范、owner 非 root、硬链接、特殊文件或其他非 root GID一律拒绝，不得用
   发布器顺手修 mode。
6. 发布与验证均幂等；部分失败只允许留下合法可重试的 mode/GID 状态，且从不产生组写权限。
7. 任务 3C 抽出既有服务账号校验、环境编码和固定 setpriv argv 构造；候选构建与探针共用，
   不跨模块导入私有函数。
8. 降权探针固定 `/usr/bin/setpriv`、`/usr/bin/env` 和词法目标 Python，清空补充组、能力与环境；
   argv 必须精确包含 `--bounding-set=-all`、`--inh-caps=-all`、`--ambient-caps=-all`；
   以 release 共享锁再 base 共享锁包围静态复验、子进程和事后复验，同时验证 UID/GID、
   `NoNewPrivs`、全部 capability、`sys.prefix`、全部受管应用导入和共享 base 中 `torch` 来源。
9. 探针只接受唯一固定单行成功回执且 stderr 必须为空；stdout/stderr 各自限制为 4 KiB；
   超时、退出非零、输出超限/漂移、
   导入系统包或任何底层失败均返回统一中文错误，不携带 argv、环境或子进程正文。

**验证：**

```powershell
python -m pytest tests/test_runtime_fd_tree.py tests/test_runtime_object_access.py tests/test_runtime_base.py tests/test_runtime_build.py tests/test_runtime_service_access.py tests/test_runtime_service_process.py tests/test_runtime_target_user_probe.py tests/test_runtime_deployment_artifacts.py -q
python -m ruff check codev_platform/runtime_fd_tree.py codev_platform/runtime_object_access.py codev_platform/runtime_service_access.py codev_platform/runtime_service_process.py codev_platform/runtime_target_user_probe.py codev_platform/runtime_deployment_artifacts.py
```

### 任务四：接入 release_staged 并旁路重建 rollback baseline

**文件：**

- 修改：`codev_platform/runtime_deployment_artifacts.py`
- 修改：`codev_platform/runtime_release.py`
- 修改：`tests/test_runtime_deployment_artifacts.py`
- 修改：`tests/test_runtime_release.py`
- 修改：相关生产部署组合测试

**TDD：**

1. target 和 baseline 必须使用同一 schema 3 base 与访问策略；满足时可复用。
2. current 缺失、schema 2、base 不同或访问证明不成立时，构建同一 baseline revision 的新版 release，
   不修改旧对象；只有新版 baseline 完整验证后才把它作为新回滚锚点。
3. 增加“target + 显式 rollback anchor”的窄激活事务并复用现有 activation lock；禁止激活 target 时
   自动把旧 schema 2 current 写入 `previous`，覆盖每个切换和补偿崩溃窗口。
4. 先完成 base/target/baseline 静态复验，再收敛三个受管父目录，然后在固定锁序内发布选中内容对象，
   完整复验后分别做 target 与 baseline 真降权探针。
5. 任一步失败时 `release_staged` 不完成；数据库、维护 marker、systemd 和现有服务均不得被触及。
6. 回执 evidence 纳入访问策略、服务 UID/GID、base、target、baseline 和双探针摘要。
7. 已完成阶段的续跑路径只做静态复验和双探针，不静默修改 mode；未完成的部分发布可幂等重试。

**验证：**

```powershell
python -m pytest tests/test_runtime_deployment_artifacts.py tests/test_runtime_production_deployment.py tests/test_runtime_deployment_coordinator.py -q
```

### 任务五：文档、全量验证、复审、提交与推送

**文件：**

- 修改：`docs/plans/roadmap-2026-07-11/daily-summary-2026-07-18.md`
- 按实际实现修改相关开发记录

**步骤：**

1. 记录 CRLF 修复后的新只读预检、权限断链根因、方案取舍和旧对象旁路迁移决定。
2. 运行聚焦测试、全部 runtime 测试、全仓 Ruff、变更文件格式、`git diff --check`。
3. 在 WSL root 下运行对象 mode/GID 集成测试，并用服务 UID/GID 对新构建 baseline/target 做真实导入。
4. 由独立角色审查身份迁移、权限边界、部署顺序和恢复语义；Critical/Important 必须归零。
5. 使用仓库身份提交；提交钩子只能推送 `origin/dev`，随后核对本地与远端完整 SHA。

### 任务六：正式部署、四库验收与最后清理

1. 用最终完整 SHA 执行 `install-wsl-runtime.sh --commit <SHA>`，保留旧失败回执。
2. 在 `release_staged` 输出中核对新 base、target、baseline 的 schema、mode、GID 与双降权探针。
3. 继续完成维护门禁、配置发布、数据库迁移、systemd 暂存、四库重建、MCP 与入口验收。
4. 核对 CUDA 可用、四项 manifest 同时指向最终 SHA、Web/Webhook/MCP/Agent 全部运行于新 release。
5. 验证 rollback baseline 可由服务用户执行，且失败补偿链成立。
6. 只有回执 `complete` 且所有验收通过后，才精确清理旧数据库、旧 schema 2 runtime 和 `.worktrees`；
   清理前再次列出目标并确认没有用户未提交文件。

## 四、完成定义

- systemd CRLF 条件守卫与 runtime 服务访问两个问题均有独立回归和复审结论。
- 新 base/release 的 root owner、服务组只读/执行和控制面私有边界均由实物证明。
- 新 baseline 与 target 都能由真实服务 UID/GID 导入，旧对象没有被原地改写。
- 正式部署回执为 `complete`，数据库和四库 manifest 指向最终 SHA，所有受管服务通过业务验收。
- 清理动作有精确清单、回滚证据和执行后复验。
