# 旧运行时可靠性任务移交审计

> 状态：旧计划完成项已固化；未完成的真实部署验收统一由 runtime-generation 计划承接。

## CodeGraph 推送协调

- Task 1–4 已完成并复审：`4092531..5c394a9`。
- 原 Task 5 的真实 WSL stdio、四类 manifest 和 MCP 验收，与 runtime-generation Integration 9–13
  重复，状态改为 `SUPERSEDED/已承接`，不再独立计数。

## runtime service access

- Task 1、2、3A、3B 已完成：`ee70acd`、`8814bc6`、`385d09b`、`96813b8`、`e2bea0a`、`355a9e3`。
- `b3749e1` 已提供 Task 3C 的 service process/target probe 叶子能力；旧 artifacts 接线后来被代际
  重构替换，后续接线由 Foundation Task 5 负责。
- 旧 Task 4 的“target/baseline 同 base”与新设计“每个 generation 独立 base”冲突，状态为
  `SUPERSEDED`；Task 5/6 的验收和清理由 Integration 9–13 承接。

## systemd CRLF 与条件门禁

- Task 1、2 和 Task 3 本地收口已完成：`ab05d41`、`65ef4b0`、`7d7bf8b`。
- 正式 WSL 重新部署和四库验收由 Integration 10–13 承接，不再单独计数。

## WSL installer 旧基线

- `9144ca9` 落地 SH/BAT，`3de1e46` 与 `9e7e364` 后续收紧拉取等待和首次候选边界。
- 旧安装器基线已完成；新 recovery launcher、代际 CLI 和 drill 脚本属于 Integration Task 7。
