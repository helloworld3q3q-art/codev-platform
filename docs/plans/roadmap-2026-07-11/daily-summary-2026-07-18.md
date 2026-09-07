# daily-summary 2026-07-18 —— WSL 生产部署状态机与四库验收收口

> 本轮完成内容寻址运行时的生产部署状态机、数据库迁移、systemd 事务、四库索引证明和
> MCP 真实工具验收的本地实现与全仓门禁。当前状态是“代码可提交”，不是“WSL 已切换完成”；
> WSL 部署、四库重建和旧运行态清理必须在推送后按受控阶段执行。

## 一、生产部署与恢复边界

- 部署流程收敛为显式计划、耐久回执、阶段更新和补偿状态机；构建、暂存、切换、数据库迁移、
  systemd 安装、索引等待和最终验收均通过窄端口组合，失败时保留可诊断证据。
- 运行时只接受内容寻址 release、已证明 wheel、固定解释器和目标提交；拒绝脏输入、路径漂移、
  版本错配、无界等待和调用方伪造的成功回执。
- systemd 安装拆分为契约、策略、文件系统适配、systemctl 适配、目标用户预检、运行身份绑定和
  事务补偿；生产模块重新满足单文件 600 行预算。
- 安装入口的基线提交回退修正为真实父提交解析，Windows/WSL 参数转交和 Linux 语法门禁保持兼容。
- 真实 WSL 预检发现私有 Gitea 返回 401 时 Git 会等待交互；安装入口现统一禁用终端、凭据管理器
  和 AskPass 交互，并为 fetch 设置 120 秒总截止时间与 5 秒进程清算窗口，避免再次无限挂起。
- 首次受管部署的基座已完成，但降权候选构建因隔离解释器没有继承 root 控制器导入路径而安全失败；
  现改为固定 `runpy` loader 显式加载目标 SHA 的只读控制器，同时复验整树与解释器 root 信任、
  隔离源码仓/候选目录/HOME，并固定可信工具路径和专用临时目录。候选工作区只允许位于
  root-owned `0711` 固定命名空间，所有纯校验先于目录变更，叶子权限通过安全目录描述符提交；
  域模型和执行适配器双层拒绝 root/UID 0/GID 0 服务账号。最终构建进程由 root 固定启动器通过
  `setpriv` 一次性降权，清空补充组与环境、清除 capability bounding set，并启用 no-new-privs，
  避免 sudo 组、PAM 或父进程环境重新扩大权限。
- 提交 `9e7e364` 的正式部署已越过 `release_staged`，进入 `maintenance_entered` 时安全停止：
  经所有权、模式、链接和大小快照证明为 root 可信的 `20-codev-cpu-embedding.conf` 使用完整 CRLF，
  旧扫描器却把其中的 `\r` 误判为不可证明语法。部署未切换旧 `current`，既有服务和数据库均保持
  原状，失败回执继续保留审计，不删除、不改写。
- 修复采用共享纯字节扫描器：在 LF 基线外只接受完整 `\r\n`，合法 LF/CRLF 混合行尾可通过；
  规范化后残留的裸 `\r`、反斜杠、NUL、非法 UTF-8 或 BOM 一律失败关闭。任一允许行尾中的未知
  `Condition*` / `Assert*` 仍拒绝，已知 `required` / `allowed` 条件原像仍按原始字节精确匹配，
  不接受 CRLF 漂移。
- 全局拒绝 BOM 同时封堵了用 BOM 隐藏条件键或伪装注释的既有绕过路径；本轮不手工重写服务器
  旧文件。必须等待包含修复的新 SHA 完成正式部署、数据库、systemd、四库、MCP、CUDA 与入口
  验收后，才允许清理旧库、旧运行态和 `.worktrees`。

## 二、数据库与四库单一真值

- Alembic 迁移包、指纹校验、PostgreSQL 协调器和运行期只读 schema 探针形成单一迁移入口；
  业务仓储不再在请求路径执行 DDL。
- SQLAlchemy 仓储基座从账户模块解耦，Job、Session、Org、User、Member 各自声明最小所需表，
  避免跨仓储误探针和隐藏建表副作用。
- `REQUIRED_INDEX_KINDS` 成为四库唯一中立契约；manifest 保留兼容别名，runner、onboard、health、
  部署回执、入口证明和生产验收均直接或经兼容视图消费同一顺序。
- runner 注册拒绝同名覆盖；四个必需 runner 与四项成功证明策略都有同序完整性测试。
- manifest 验收要求四项均属于同一目标提交和运行修订，`code_vec` 依赖精确的 CodeGraph 事实；
  pending、旧提交、空 revision、缺项、重复或乱序均不能假绿。
- WSL 实物验收补齐 CUDA 发行包清单兼容：多个 NVIDIA wheel 只有在 RECORD 摘要和大小完全一致时
  才可共同声明命名空间文件，全部 owner 进入规范证据；冲突声明仍立即拒绝。
- `cuda-bindings` 与 `setuptools` 的可执行 `.pth` 仅按固定文件名、唯一 owner 和已审摘要放行，
  内容解析复用同一次安全快照、聚合内存限制为 1 MiB，并在策略检查后再次复验，阻断替换竞态；
  其他启动代码继续拒绝。
  真实 143 个发行包、31,858 个文件清单已完成逐文件证明。

## 三、MCP 真实业务验收

- platform-docs、CodeGraph、Graph、Agent Memory 分别使用固定只读工具与工具级成功语义，
  HTTP 可达、空对象、错误状态或形状相似不再算成功。
- Graph `search_nodes` 与 Agent Memory `recall` 在服务出站边界真实回显 `project_id/tool`，
  验收端强制与受信路由一致；Graph 的纯查询 `dispatch` 仍保持与引擎直读结果一致。
- SDK 回执只接受唯一 JSON `TextContent`，拒绝 `structuredContent` 双表示、多个 payload、非文本内容、
  超过 1 MiB、非有限 JSON 值和任意层级重复键。
- 证据摘要包含规范化业务值、项目、服务和工具，不包含 token；下游异常统一脱敏。

## 四、质量门禁与复审

- 完整 Python 测试：`4661 passed, 203 skipped`；仅有一条第三方 Starlette/httpx 弃用警告。
- 本次候选构建安全边界收口后，69 个 runtime 测试文件回归为 `662 passed, 73 skipped`；
  变更文件 Ruff 与格式检查、WSL 安装脚本语法检查均通过。
- 全仓 Ruff 从 91 个存量问题清理到零，未新增忽略项、`noqa` 或 unsafe fix。
- MCP/Graph/Memory 攻击性回归、systemd 事务、数据库仓储、四库契约和安装器均有独立定向回归。
- 三个只读复审角色分别检查依赖边界、组合单一真值和回执安全，最终均无 Critical/Important。

## 五、提交后的受控执行顺序

1. 仅推送 `origin/dev`，禁止推送 GitHub/origin。
2. 在 WSL 单一正式环境拉取精确提交，执行只读预检后构建并验证新 release。
3. 通过部署状态机迁移数据库、安装并切换 systemd 运行态，不手工覆盖状态文件。
4. 重建 `chroma → codegraph → ingest → code_vec`，等待四项 manifest 精确指向目标提交。
5. 验证 MCP、Webhook、CUDA、健康面、运行身份和失败恢复链路。
6. 只有新运行态和回滚证据全部成立后，才精确删除旧库、旧 release 和 `.worktrees`；不得提前清理。
