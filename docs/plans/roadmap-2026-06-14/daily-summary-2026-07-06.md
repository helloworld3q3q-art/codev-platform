# daily-summary 2026-07-06 —— P3 多仓多根索引 fan-out 首批落地

> 承 2026-06-14 产品化迭代 P3。用户明确要求继续做多仓多根索引,但不要堆代码:要求模块化、低耦合、可扩展、分阶段可验。
> 本日按低风险切片推进,不改队列 schema、不合并多个 codegraph.db、不碰外部 codegraph MCP 代理协议。

## 一、已落地

### 1. code_vec / recall 多仓 fan-out

提交:`0812b8b feat(recall): 支持多仓代码向量召回`

- `core/repos.py` 新增 `RepoSpec` / `project_repo_specs()` 作为主仓 + extra_repos 的中性真值源。
- 主仓 ref/file/id 保持旧语义不变;extra repo 用 `tag::` 前缀做命名空间,避免跨仓同路径/同符号碰撞。
- `code_vector_store` 按 RepoSpec 遍历多仓 codegraph 节点;主仓 codegraph 缺失保持旧失败语义,extra 缺失 fail-soft skip。
- `recall_code` 的 codegraph lane 改成跨 repo 搜索并合并结果;fusion/reranker/weights 不感知 repo 维度。
- `reindex --codegraph` stage 内部按 RepoSpec 逐仓 sync,extra project-id repo 用自己的 `source_project_id` 做 ensure-link。

### 2. extra repo 变更反向触发主项目 reindex

提交:`0a94264 feat(reindex): 支持 extra 仓反向触发重建`

- `core/repos.py` 新增 `impacted_project_ids_for_repo()`。给定发生变更的仓根,反查需要刷新的逻辑项目集合。
- 本地 git hook `_dispatch_reindex()` 从单 pid 入队改为多目标入队:extra 仓变更时,child project 和引用它的 parent project 都会排队。
- webhook 入口同步支持反向触发,服务器收到 extra repo push 时也会触发父逻辑项目的 `codegraph -> ingest -> code_vec`。
- 不改 reindex queue schema,继续利用现有 `(project_id, kind)` 合并和 worker 串行消费。

### 3. agent 内置 codegraph 工具跨仓查询

提交:`d00de6f feat(agent): 支持 codegraph 工具跨仓查询`

- `agent.tools.codegraph` 的 `codegraph_search / callers / callees / trace` 改为按 RepoSpec 读取多个 codegraph.db。
- 主仓返回位置保持 `src/x.py:line`;extra repo 返回 `repoTag::src/x.py:line`。
- 保留集中路径 codegraph.db 的兼容逻辑,不破坏旧单仓部署。

## 二、验证

已跑并通过:

```powershell
python -m pytest tests/test_agent_tools.py tests/test_agent_codegraph_trace.py tests/test_agent_project_routing.py tests/test_graph_ingest.py tests/test_reindex_ingest_stage.py tests/test_webhook_body_limit.py
git diff --check
git push
```

结果:`60 passed, 1 warning`。warning 来自 Starlette/TestClient 上游弃用提示,非本次变更引入的业务失败。

push 前 graph audit 通过:

- `codev-platform` OK clean
- `openclaw-stock` OK clean

## 三、架构取舍

- **不合并多个 codegraph.db**:每仓继续保留自己的 codegraph 索引,读侧 fan-out 合并。这样不碰外部工具库结构,失败边界清楚。
- **不改队列 schema**:extra repo 反向触发只扩展入队边界,消费模型仍是 project/kind 串行 worker。
- **repo 维度只放在边界层**:RepoSpec 只影响构建/召回/agent 工具 adapter;fusion、reranker、weights 等共享算法不引入 repo 概念。
- **主仓兼容优先**:主仓 id/ref/file 不加前缀,保证现有单仓项目和历史索引行为稳定。

## 四、当前剩余

P3 已从"待做"推进到"核心链路部分完成":

- ✅ code_vec 多仓构建
- ✅ recall_code 多仓召回
- ✅ reindex codegraph/code_vec 多仓阶段
- ✅ extra repo 变更反向触发父项目 reindex
- ✅ agent 内置 codegraph 工具跨仓查询
- ⏳ Web GraphAPI 的 codegraph 只读接口仍主要按单 project DB 读取,下一刀可做 read-side fan-out
- ⏳ 外部 `codegraph.server` MCP 代理仍是 per-project 主仓 stdio backend;该层要做多后端合并需先固化工具返回协议测试,不能贸然堆

## 五、顺带发现

post-commit 中出现:

```text
[repos] 跳过相对 extra_repos 'sample-project-alpha'(须绝对路径或 project-id ref, 防 CWD 串目录)
```

含义:本机 config 未把 `sample-project-alpha` 解析成 repo_path 时,meta 里的该条会被当作相对路径并 fail-closed 跳过。索引不因此失败,但真实多仓项目要完整工作,本机应在 `projects.sample-project-alpha.repo_path` 登记该仓路径,或后续优化提示文案区分"未登记 project-id"与"相对路径"。
