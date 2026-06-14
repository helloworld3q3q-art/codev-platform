# Phase 1 原子索引切换(atomic handoff)— 实现 plan(2026-06-14)

> 蓝图 Phase 1 重型项之一(`code-intelligence-platform-plan-2026-06-07.md` §Phase 1 "atomic handoff: 新索引构建成功后再切换 current pointer")。
> 三兄弟对抗面板(YAGNI / 架构纯度 / 多机 arc ROI)收敛后**纠正切片方向**落地。

## 〇、缘起:面板纠正了 Phase 1 的切片选择

原判("做 depends_on 声明式排序 / atomic handoff 缓做")被面板双向推翻:

- **YAGNI 兄弟(实读代码)**:reindex stage 仅 **4 个**,顺序集中在 `ops/reindex/commands.py` 单函数线性块,worker 纯串行不管序;code_vec 读新鲜 codegraph 的真依赖已被同子进程 `['--codegraph','--code-vec']` 纵深解决。→ `depends_on`+toposort 是**负收益重构**(把单处可读线性拆成两层),**砍掉**,触发=stage≥6 且真跨 job 序约束。
- **多机 arc 兄弟**:atomic handoff 才是主线(多机/多组织/多人连用)**直接触发的刚需** —— 多 reader 并发读共享索引,而 Stage C 的 PG 后端只挡"写撞写",**没覆盖"writer 重建时 reader 读半成品"的读侧路径**。

## 一、定位与消费方(为什么做)

**Phase 1 atomic handoff = 让 full rebuild 期间 reader 永不读到半成品索引。**

**真实痛点(已实证,非臆想)**:`daily-summary-2026-06-12 §十.1` —— full rebuild 原地 rmtree chroma 库,重建中途 SIGKILL 把库半写坏在 flush 中途,reader(serve-mcp daemon)读到 `database disk image is malformed`,**search_docs 全项目下线**。单机已踩;多机多 reader 并发放大此窗口。

**消费方 = 所有 chroma reader**:platform-docs daemon(search_docs)、code_vec 召回 lane(recall_code)。它们经 `chroma_docs_dir(pid)` / `_code_vec_persist_dir(pid)` 拿库路径。

**scope 边界(只做最痛的,必要才上)**:
| store | 撞读窗口 | 本轮 |
|---|---|---|
| chroma docs `docs/<pid>/` | **大**(rmtree 重建,实测半写坏)| ✅ 做 |
| chroma code_vec `code_vec/<pid>/` | **大**(同构 rmtree 全量重建)| ✅ 做(复用同模块)|
| codegraph `codegraph.db` | 小(sqlite WAL,reader 读快照)| ❌ 不做 |
| graph store `<pid>.sqlite` | 小(sqlite 单文件事务)| ❌ 不做 |

## 二、核心设计:blue-green 双缓冲 + 跨平台原子 pointer

writer 不再原地 rmtree+重建,而是**建到 side build 目录**,建成后**原子切 current pointer**;reader 读 pointer 指向的目录 → 全程读旧 build,绝不撞半成品。失败的 side build 不影响 current。

```
data/chroma/docs/<pid>/
├── current.json          # {"build": "<build_id>"} —— pointer(唯一真值)
├── builds/
│   ├── <old_build_id>/   # reader 持有的旧连接仍读这里(GC keep≥2 保命)
│   └── <new_build_id>/   # writer 建好后 commit,current 指向它
├── .reindex.lock         # 控制路径:落 base 根(不进 build dir)
└── .manifest.json 戳     # 控制路径:落 base 根
```

**为什么 pointer 文件 + `os.replace`,不用 symlink / 目录 rename**(关键决策,经得起证实):
- **symlink**:Windows 需开发者模式/管理员权限 → 不可移植(违反平台跨平台约束)。
- **rename 整个 build 目录覆盖 live**:目标非空时 POSIX 报 `ENOTEMPTY`、Windows 报错 → 不可跨平台原子。
- **pointer 文件 + `os.replace(tmp, current.json)`**:POSIX `rename(2)` + Windows `ReplaceFile` 都保证**同卷原子替换单文件** → 跨平台稳,且 pointer 是单一真值不会半写。

**向后兼容(零迁移)**:`resolve_current(base)` 无 `current.json` 时**退回 base 本身** → 现有所有原地库继续工作,直到第一次 full rebuild 才走新布局。无需迁移脚本、无需停机。

## 三、模块分层(轻量聚合 / 低耦合 / 可扩展)

```
core/index_handoff.py        纯核(functional core)—— 唯一新文件
  resolve_current(base) -> Path        reader 入口:current 指向 / 无则退 base
  begin_build(base, build_id) -> Path  writer 入口:side build 目录(同 id 先清)
  commit_build(base, build_id)         原子切 current(tmp + os.replace)
  gc_builds(base, keep=2) -> list[str] 删非 current 旧 build,保最近 keep
  纯路径 + os.replace 逻辑,不碰 chroma/sqlite,可脱服务单测(Windows 可验)。

core/paths.py                适配:chroma_docs_dir 数据路径走 resolve_current
recall/code_vector_store.py  适配:_code_vec_persist_dir 同构复用
chroma/indexer.py            编排:full rebuild → begin/commit/gc;增量 → 原地
chroma/_models.py 等 reader   零改(经 chroma_docs_dir 自动拿 current)
```

**设计模式**:blue-green 双缓冲(current pointer + versioned build dirs);functional core(纯函数无状态,确定性可测)。**刻意不上**:不引编排框架、不做事务管理器(单 writer 串行 + 原子 pointer 已够)。

**关键低耦合点**:reader 经 `chroma_docs_dir` 单一入口拿路径,内部改走 `resolve_current` → **reader 几乎零改**;writer 在 full/增量分支显式调 begin/commit。数据路径(走 current)与控制路径(`.reindex.lock` / 新鲜度戳 / manifest 落 base 根)严格区分。

## 四、关键决策

**① full rebuild 走 side+swap,增量原地** —— atomic handoff 与增量索引(变更0→0.9s)冲突:每次 side full rebuild 会丧失增量价值。故只 full rebuild(`--force` / 参数指纹变 / 库损坏自愈)走 blue-green;增量改少量 chunk、撞读窗口极小(chromadb WAL 下 reader 读快照),保持原地。**这是"必要才上"的切片边界**。

**② build_id 由调用方传入,模块不自产** —— `index_handoff` 纯函数可测(不引 `time`/`random` 不确定源);indexer 传 `git_commit[:12]`,无 commit 时传调用方给的时间戳串。

**③ GC keep=2 不激进** —— commit 后,reader 的旧连接可能仍读旧 build;保最近 2 个(current + 前一个)给旧连接读完。不做基于引用计数的精确回收(过度设计),keep=2 + 下次 rebuild 时 GC 足够。

**④ base 根旧库残留** —— 首次切换后,旧的原地库文件(`chroma.sqlite3` 等)留在 base 根成残留(不能删:commit 前 reader 还在读它)。提示运维首次切换确认后手动清一次,或后续加一次性迁移(本轮不做,避免删正在读的库)。

## 五、分阶段交付(模块化 / 每阶段独立可验)

| 阶段 | 交付 | 验证 | 风险 |
|---|---|---|---|
| **1(本轮)** | `core/index_handoff.py` 纯模块 + 完整单测 | `pytest tests/test_index_handoff.py`(Windows 可跑)| **零**(不碰服务/索引)|
| 2 | chroma docs writer(full→side+swap)+ `chroma_docs_dir` 适配 + reader 验证 | WSL 真机 full rebuild + daemon 读不中断 | 中(改 writer/reader)|
| 3 | code_vec 复用同模块(`_code_vec_persist_dir` + `build_code_vector_index`)| WSL code_vec 重建 + recall_code 不中断 | 中 |
| 4(可选)| manifest 记 build_id + `index status` 显示当前 build | 单测 + CLI | 低 |

**阶段 1 是干净起点**:纯路径逻辑、Windows 可验、零服务风险,且是阶段 2/3 复用的单一真值核。

## 六、影响面 / 回退 / 禁改

- **影响消费方**:阶段 2/3 才触及 reader/writer;阶段 1 仅新增文件,零现有调用方影响。
- **回退**:`resolve_current` 无 pointer 退回 base = 老路径;阶段 2/3 若出问题,删 `current.json` 即回原地布局(reader 自动退回 base)。
- **禁改**:不动 `data/`/`.venv/` 运行态;不并发重建(写侧串行,workflow §8);真机重建走 reindex worker 非手动 ingest([[graph-rebuild-via-worker-not-manual-ingest]]);WSL 运维走 PowerShell([[codev-platform-ops-via-wsl]])。
- **禁止 AI 自动执行**(workflow §11):`serve-mcp start` / 全量 reindex —— 阶段 2/3 真机验证需用户授权或走单项目 enqueue。

## 七、不做(防过度设计)

- 不做 sqlite 系(codegraph/graph)handoff —— WAL 已给 reader 快照隔离,无实测痛点。
- 不做增量的 side build —— 破坏增量价值,撞读窗口本就极小。
- 不引 symlink / 目录 rename —— 不跨平台。
- 不做引用计数精确 GC —— keep=2 足够,过度设计。
- 不做 Phase 1 的 depends_on/toposort、dashboard、count 回填 —— 面板判 YAGNI / trigger-gated。
