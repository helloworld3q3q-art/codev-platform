# openclaw-stock 索引完整性最小恢复计划

> **状态：** 已完成（运行态发布、队列恢复、正常 hook 写入和独立持久化证明均已通过）。
>
> **目标：** 修复 Chroma 文档索引的持久化完整性证明边界，并恢复 `openclaw-stock` 与 `codev-platform` 在 WSL 正式环境中的文档索引，使其精确覆盖当前最新目标；不影响已成功的 CodeGraph、统一图谱 ingest 与代码向量索引。

## 一、触发证据

- 初始检测时，本地业务仓 `C:\workspace\project` 的 HEAD 为 `f2d716207ab6151d487b2207277e3c35cc805ec6`，工作树干净。
- WSL worker 已成功写入该提交的 `codegraph` 与 `ingest` manifest；`chroma`、`code_vec` 均为 `status=failed`，且队列为空。
- `chroma` 完整性证明发现 collection 为 6463 条、manifest 期望 6469 条，说明历史集合缺少 6 条记录。
- `code_vec` 当前 collection 为 22626 条、checkpoint 期望 22646 条，说明历史集合缺少 20 条记录；失败关闭后 manifest 已被撤销。
- 恢复开始后用户又推送新提交 `73fb0880973ed069ff92b3a3a390ca141864d345`；Windows 与 WSL 项目仓已一致到该提交，既有 hook 已成功更新 `chroma`、`codegraph`、`ingest`，并自动投递最新提交的 `code_vec` 恢复任务。旧目标 `f2d7162` 因此被最新目标替代，不重复入队。

## 二、等级、影响面与约束

- **等级：** L4；涉及 WSL 长驻 worker 与两类索引运行态。
- **范围：** 仅项目 `openclaw-stock` 的 `chroma`、`code_vec`；目标提交固定为当前 `73fb0880973ed069ff92b3a3a390ca141864d345`。
- **不在范围：** 不重跑 `codegraph`、`ingest`，不修改业务仓源码、运行时、base、依赖、服务配置、MCP 配置或项目登记表。
- **禁止：** 不使用 `--force`，不手工删除 Chroma/SQLite/manifest 文件，不直接调用 `systemctl`，不建立第二套环境，不推送 GitHub。
- **回退：** 两个索引器在 manifest 缺失时均使用隔离 side-build；旧 current 保持可读，只有独立完整性证明通过后才原子切换。失败时不发布新 build。

## 三、为什么普通增量不足

本次不是瞬态 compaction 删除错误，而是 collection 与其历史登记清单发生结构性差异。继续在当前 collection 上增量写入无法安全补齐未知缺失 ID，故完整性门禁正确拒绝发布并撤销 manifest。撤销后的下一次官方队列消费会由既有索引器自动选择最小范围的 full side-build；该动作不等同数据库删除或全平台重建。

## 四、执行顺序

1. 记录本计划并登记 roadmap；复核 worker 空闲、提交与失败证据。
2. 优先观察既有 hook 对当前目标的自动队列消费；仅在对应 lane 失败且队列空闲时，才通过当前 WSL runtime 的 `reindex-queue enqueue` 补投精确项目和目标提交。
3. 保持串行 worker 消费；只观察队列、CPU/心跳和受控回执，不手工干预索引文件。
4. 完成后读取两类 manifest，确认 `status=ok`、提交和 target 均精确匹配；同时复核既有两类成功 manifest 与四 MCP/worker 状态。
5. 记录运行态发布、独立持久化证明与队列回执；提交本计划后，由正常 hook 执行一次 `codev-platform/chroma` 文档增量，作为新证明边界的真实写入验收。
6. 已确认该正常 hook 的 manifest、集合实数和四服务状态均通过，更新本计划和目录登记为完成。

## 五、验证标准

- `reindex-queue status`：worker running、pending=0、active=0。
- `chroma`、`code_vec` manifest：`status=ok`、`commit` 与 `target_commit` 均为目标提交、`process_rc=0`。
- `codegraph`、`ingest` manifest 继续保持目标提交的 `status=ok`。
- `serve-mcp status`：四个端点均为 OK，worker 为 idle。

## 六、完成回执

- 用户后续推送的最新提交 `73fb0880973ed069ff92b3a3a390ca141864d345` 已由既有 hook 自动进入 WSL 串行队列；Windows 与 WSL 项目仓 HEAD 一致。用户尚未提交的业务文件保持原样，未被本次操作触及。
- `chroma`、`codegraph`、`ingest` 均已为该目标提交写入 `status=ok`、`process_rc=0` 的 manifest；`code_vec` 隔离 side-build 在 601.83 秒内完成独立完整性证明并原子发布，同样为 `status=ok`、`process_rc=0`。
- 运行态实数核验：Chroma 当前 collection 与 manifest 均为 6475 条；代码向量 collection、manifest、checkpoint 均为 22704 条。此前缺失记录的旧 build 未被伪造为成功。
- 最终 worker 为 running/idle，pending=0、active=0；`platform-docs`、`codegraph`、`agent-memory`、`graph` 四个 MCP 端点均为 OK。
- 未使用 `--force`、手工删除索引/数据库文件、重建运行时/base/依赖或重跑已成功的 CodeGraph、统一图谱。

## 七、后验缺口与修复裁决

- 初次恢复后，新的文档提交再次暴露两项不同但同属 Chroma 持久化边界的问题：`codev-platform` 的文档 collection 为 3711、manifest 期望 3718；`openclaw-stock` 的新目标 `79ce9af2c04e1f4f0607354b76bbb1c54835549e` 在删除旧 chunk 时失败。两者均被 fail-closed 正确拦截，不能宣告任务完成。
- 源码核对表明文档 indexer 在写入后直接使用同一 `PersistentClient` 的 collection 证明并发布 manifest；而 `code_vec` 已先关闭写端，再由独立短进程打开持久化 collection 证明。前者可能观察到未完全落盘的进程内视图，不能作为发布依据。
- 裁决：新增文档索引专职的独立进程证明适配层；indexer 仅负责关闭写端、调用该适配层并在成功后发布。探针不可用采用有界重试，结构性不一致立即撤销 manifest；不把文档索引耦合到 `code_vec` 或其业务 manifest。
- 修改范围仅限 Chroma 证明适配、indexer 生命周期调用及定向单元测试。验证通过后以既有薄运行时前移发布修复版本，再只重投失败的 `codev-platform/chroma` 与 `openclaw-stock/chroma` 最新精确目标；不重跑成功的 `codegraph`、`ingest`、`code_vec`，也不使用 `--force` 或手工删除。

### 当前实现与本地验证

- 已新增 `chroma/document_proof` 与 `chroma/document_probe`：父进程只解析受限 JSON 回执并执行有界暂态重试；子进程只读已落盘的 manifest 与 collection，返回 count/pid/错误类型，不回传路径或正文。
- `chroma/indexer` 已改为保存 manifest 后关闭写入客户端，再调用独立 probe；写端没有关闭接口、关闭失败、探针结构失败或暂态重试耗尽均撤销 manifest 并拒绝发布 current/reload stamp。
- 新增关闭顺序、探针协议、暂态重试和结构失败撤销回归。Chroma/代码向量/队列相邻组合验证：`144 passed, 3 skipped`；打包、执行器、runner 时限与薄发布组合验证：`75 passed`；Ruff 与 `git diff --check` 通过。
- 官方 thin promotion 已成功前移到 runtime revision `7d9869a0b39d6452b6df03d1e26bac9fc5bb16c4`、release `c529eb36789e51e9fd3dad2eccf426379d824c1c62e319a15d4a2dbea6ca7d93`；复用既有 base，未下载依赖、未重建数据库或索引。
- 受控队列在新运行态中空闲：`codev-platform` 四类 manifest 均已对齐 `7d9869a0b39d6452b6df03d1e26bac9fc5bb16c4`；`openclaw-stock` 四类 manifest 均已对齐用户最新提交 `9d639b6e7f884dc180077b882235276ccced613c`。因此无需人为重复入队已成功的 lane。
- 使用新运行态的独立只读 probe 直接核验 current build：`codev-platform` manifest 与物理 collection 均为 3720 条，`openclaw-stock` 均为 6494 条；随后 HTTP 平台健康视图也显示相同的 3720/6494 计数，四个 MCP 端点均为 OK。
- 收口记录提交 `dc1f4e0cc109eb8759b1725bb640363a87d4e6a9` 后，正常 hook 的 `codev-platform/chroma` 已对齐该提交并成功完成真实写入；队列回到 `pending=0`、`active=0`。

## 八、最终回执

- WSL 正式运行态为 revision `7d9869a0b39d6452b6df03d1e26bac9fc5bb16c4`、release `c529eb36789e51e9fd3dad2eccf426379d824c1c62e319a15d4a2dbea6ca7d93`，沿用既有 base；未下载依赖、未重建 runtime、数据库或基础索引。
- `codev-platform` 的文档索引已在收口记录 `dc1f4e0cc109eb8759b1725bb640363a87d4e6a9` 的正常 hook 中完成真实写入并通过独立 probe（manifest 与物理 collection 均为 3720 条）。后续仅文档提交始终只更新同一 Chroma scope；CodeGraph、ingest、代码向量保持代码提交 `7d9869a0b39d6452b6df03d1e26bac9fc5bb16c4` 的已验证结果，不做无输入变化的重复构建。
- `openclaw-stock` 的本机与 WSL HEAD 均为 `9d639b6e7f884dc180077b882235276ccced613c`；Chroma、CodeGraph、ingest、代码向量四类 manifest 均为 `status=ok` 且精确对齐该提交。独立 probe 的 manifest 与物理 Chroma collection 均为 6494 条。
- worker 为 running/idle、`pending=0`、`active=0`；`platform-docs`、`codegraph`、`agent-memory`、`graph` 四个受管端点均为 OK，HTTP 平台健康视图与两项物理 Chroma 计数一致。
- 全程未使用 `--force`、手工删除 collection/SQLite/manifest、手工 systemd、运行时/base/依赖重建，也未重复投递已成功的索引 lane。
