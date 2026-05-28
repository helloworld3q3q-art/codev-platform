# PR body 草稿:feat: AI 协作工具栈多项目多团队部署 (Phase 0 + a-d 落地)

> 用法:打开 https://github.com/helloworld3q3q-art/platform/pull/new/feat/team-deploy ,把下面正文贴进 description 即可。
> Title 建议:`feat: AI 协作工具栈多项目多团队部署 (Phase 0 + a-d 落地)`

---

## Summary

- 单项目工具栈改造为多项目:chroma collection 加 `<project_id>__` 前缀,cross-link DB per-project 子目录,codegraph 天然 per-repo
- 新建 `tools/_platform/` 共享模块(project_id resolver + paths 约定,本地/server 双模式)
- 新建 `tools/claude-platform/` CLI(`init` / `current` / `list-projects` / `validate`)+ `platform-meta/` 跨项目登记表
- design doc 落 15 决策点定稿 + Phase a-d 实施纪要 + scope 收口(本地原型,server 化能力留接口)

## Decisions

详见 `docs/plans/roadmap-2026-05-27/team-deploy-2026-05-27-design.md` §十一/十二:

- **D1** project_id = `<slug>`(扁平连字符),当前 = `openclaw-stock`
- **D2** personal 本地 only(无 server 同步)
- **D3** 全 24 rules + skills 留 project,platform 层暂空 organic 抽取
- **D5** 部署拓扑 A(单 server 多 project)
- **D13** platform-meta 当前内嵌仓内,未来抽独立私有仓

## Audit 收尾

8 项 fix(BLOCKER + WARN + NIT):

| 编号 | 内容 |
|---|---|
| B1 | launcher 检测 daemon `/health` 缺 `project_id` 时硬失败 + kill 指引 |
| B2 | `build_index` 跑完检测 legacy DB 残留时 stderr 提示重启 |
| W1 | `resolve_from_request` header 大小写完全不敏感 |
| W2 | cross-link `CROSS_LINK_DB` explicit 时 `PROJECT_ID=None` |
| W4 | launcher spawn 后二次 `project_id` 核对 |
| W5 | `index_docs` 增量时提示手动 prune legacy collection |
| N3 | `LEGACY_COLLECTION_NAME` 字面量(可读性) |
| N4 | `_smoke_test` 输出改 ASCII(防 GBK console 乱码) |

## Test plan

- [x] 新 daemon `/health` 含 `project_id=openclaw-stock`
- [x] chroma `openclaw-stock__platform_docs` 全量 4419 chunks(legacy 4389 保留备份)
- [x] cross-link `data/codegraph_ext/openclaw-stock/cross_layer.sqlite` 写好(legacy 保留)
- [x] resolver 三态(env / `.claude/project.json` / 硬失败)全测
- [x] env override 切 `demo-project-x` 验证 collection / DB 路径完整隔离
- [x] CLI 4 命令(`init` / `current` / `list-projects` / `validate`)全跑通
- [x] `platform-meta/projects/` 登记 `openclaw-stock` + `codev-platform-widget` 两项目
- [x] ai-health 显示 project_id(顶部 + daemon 行)
- [x] pre-push 6/6 gates 全过
- [x] smoke test(`tools/_platform/_smoke_test.py`)全绿

## 已知遗留(低价值,后续单独 PR)

- **W3** `sys.path.insert` 重复(~6 个文件);可抽 `_platform/__init__.py` self-bootstrap
- **N1** `paths.py` `parents[2]` 对未来重构脆性(优化点而非 bug)
- **N2** CLI `input()` 非交互场景应加 `--non-interactive` flag

## 部署影响

- 当前活着的 chroma daemon / cross-link MCP server 是 commit 前启动的旧代码;merge 后需 **kill daemon + 重启 Claude Code** 让新代码生效
- 不重启的兼容期:新代码 fallback 到 legacy 不破坏现状;但建议 merge 当天就重启
- legacy collection / DB 保留为 backup,确认稳定后可手动 prune(各模块都打印了 prune 命令)

## Commits (9)

```
3adb3e1 feat(ai-health): 顶部 + daemon 行显示 project_id
d1974ef feat(team-deploy): 注册 codev-platform-widget 作第二个项目
1e61e72 fix(team-deploy): 收 audit W2/W5/N4
0aed3c1 fix(team-deploy): 收 audit W1/W4/N3
373719a docs(log): 2026-05-27 工业化进展盘点
8f40e13 docs(team-deploy): Phase 0 design doc + CLI 雏形 + platform-meta 骨架
2e17bb1 feat(cross-link): 接入多项目 namespace
11b22b1 feat(chroma): 接入多项目 namespace
bdf7774 feat(tools): 加 _platform 共享模块
```
