# 事故复盘 — chromadb 1.5.9 多 flush compaction 损坏 + 误诊连锁(2026-06-05)

> 类型:工具栈事故(chroma 索引库被重建操作搞坏 + 一连串误诊)
> 影响:platform-docs `search_docs` 一度对所有项目全挂;openclaw-stock collection 多次被损坏后重建恢复。
> 关联:`.claude/rules/ai-tools-mcp.md`、`codev_platform/chroma/indexer.py`、`requirements-runtime.txt`(`chromadb>=1.5.9`)。

---

## 1. 触发与现象

dogfood 一次 agent 跑(openclaw-stock 项目)里,`search_docs` 对 "项目管理 添加项目类别" 返回的是 **codev-platform** 的文档。由此开始排查,最终把 chroma 库彻底搞坏(`database disk image is malformed`),`search_docs` 全项目不可用,再用单 upsert 重建恢复。

## 2. 误诊连锁(核心教训)

层层误判,每一层都被下一步推翻:

| # | 当时结论 | 实际 | 证伪方式 |
|---|---|---|---|
| 1 | search_docs 忽略 project_id,多租户路由失效 | 路由正常(contextvar 正确传到 call_tool) | 插桩日志 `call-get pid=openclaw-stock`、`vid` 全程一致 |
| 2 | openclaw collection 被"污染"成 codev 文档 | **设计如此**:`platform/.claude/index.json` 的 `external_doc_paths` 故意引 `../codev-platform/docs/**` 作跨仓真值源 | 读 index.json + 扫 collection(3811 REL 本地 + 1343 ABS 外部=设计内) |
| 3 | data/chroma 路径 / 磁盘 I/O 坏 | 同 ext4 同盘;`/tmp` 同样失败 | df 同 `/dev/sdd`;dmesg 无 I/O error |
| 4 | 并发写(reindex-queue worker + 我)导致 | 停掉一切、零持有者仍崩 | lsof 确认 0 holder 后重建仍 `disk I/O 522` |
| 5 | 文件未 settle / flush 时机 | sync+8s 仍崩 | 加 sync+delay 无效 |

**真根因(第 6 层才到)**:chromadb 1.5.9 的 compaction —— 见 §3。

**根本教训**:**动共享数据(尤其破坏性 `--force`)前,先确认"这真是 bug 吗"。** 第 2 层只要先读一眼 `index.json` 就知道"污染"是设计,整起事故可避免。误诊 + `--force` 猛刷把"轻微观感问题"滚成"全库损坏"。

## 3. 真根因:chromadb 1.5.9 多 flush compaction 损坏

确定性复现:

- **单 collection 建库**:永远成功(codev 1632 / openclaw 5154,在 `/tmp` 与首个 build 都过)。
- **往已有一个 collection 的库再 bulk-build 第二个**(indexer 每 100 chunk 一次 `col.upsert`,即"多次 flush"):**必崩**,报 `Error purging logs` / `Failed to pull logs from the log store` / `database disk image is malformed` / `disk I/O error (522 SHORT_READ)`(错误随机但同源)。
- 与构建**顺序无关**、与**并发无关**、与**路径无关**、与 settle delay 无关。
- 早期 throwaway 测试:**单次** upsert 600 chunk 进新 collection(库里有别的 collection)**成功** → 锁定是"多次 flush + compaction"触发。

为什么平台之前没事:原 3 个 collection 是**增量**(每次 commit 改几个文件,小 upsert)建起来的;**只有全量 `--force` bulk 重建**(几十次 flush)才触发。

查询侧不犯:重建完**干净重起 daemon** 后,两个项目 search 全部正常(`hit=5`,rerank 分高)。之前查询报错是"重建过程中起来的陈旧 daemon"看到半成品所致。

## 4. 修复

- **代码**:`indexer.py` 的 flush 阈值 `BATCH` 默认 100 → `int(os.getenv("PLATFORM_INDEX_FLUSH_BATCH","50000"))`。现实 collection(几千 chunk)即"单次 upsert",彻底绕开 bug;env 可调小兜底超大库(代价见注释)。
- **恢复**:停 daemon + reindex-queue worker → 确认 0 chroma 持有者 → `rm -rf data/chroma` → 逐项目单 upsert 重建(`PLATFORM_INDEX_FLUSH_BATCH=100000`)→ 干净重起 daemon → 实测 search OK。
- **待办(未做,需拍板)**:`chromadb>=1.5.9` 是否钉一个修了 compaction 的稳定版本,留待下次依赖升级一并评估;单 upsert 默认已让当下不依赖它。

## 5. 预防 / 落地纪律

1. **改/重建共享 chroma 前先确认是不是真 bug**;能只读验证(读 index.json / 扫 collection metadata)就别上 `--force`。
2. **全量重建必走单 upsert + 独占**:停 daemon + reindex-queue worker,确认 `lsof`/`/proc/*/fd` 无 `data/chroma` 持有者再建(chromadb 持久库非多进程写安全)。
3. **重建 / 改 schema 后必"干净重起 daemon"**:`pkill -9 -f chroma.server` → `serve-mcp start --wait` → 等暖机再判断,别拿陈旧 daemon 的报错下结论。
4. **chroma 是派生索引**:源 md 文档/源码/git 全程无损;再坏也能重建——但别因此随意 `--force`。

## 6. 已有 assertion 兜底

⚪ 纯复盘。candidate:
- 缺:全量重建脚本自动停 worker + 校验 0 chroma 持有者(防多进程写)。
- 缺:CI/启动校验 chromadb 版本在"已知安全"集合内(防 compaction 回归)。
- 缺:重建后自动"干净重起 daemon + 一次 search 探针"确认可服务。
