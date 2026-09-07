# Open-source Release Checklist / 开源发布检查清单

Use this checklist before making the working tree a public open-source repository. Complete every applicable item before changing repository visibility; do not publish internal-only materials, credentials, or runtime data with the source.

本清单用于将当前工作树发布为公开开源仓库。完成每一项后再变更仓库可见性；不要把仅适用于内部环境的资料、凭据或运行数据一同公开。

## Project Owner Confirmation / 项目所有者确认

- [x] The project owner selected Apache-2.0; root [LICENSE](LICENSE), [NOTICE](NOTICE), and `pyproject.toml` metadata now agree.<br>项目所有者已选择 Apache-2.0；根目录 [LICENSE](LICENSE)、[NOTICE](NOTICE) 与 `pyproject.toml` 元数据现已一致。
- [x] The project owner confirmed that all historical repository commit authors are the same rights holder and authorized this public release.<br>项目所有者已确认 Git 历史中的全部提交作者均为同一权利人，并已授权本次公开发布。
- [ ] Complete the separate audit of imported code, documentation, model configuration, example data, and other third-party material before making the repository public.<br>公开仓库前，仍须完成对引入代码、文档、模型配置、示例数据及其他第三方材料的独立审计。
- [x] Audit all Git references, not only the current working tree, with high-confidence key patterns; no potential private-key, OpenAI, GitHub, AWS, or Slack credential matches were found. If a real credential is found later, revoke or rotate it before deciding whether history must be rewritten.<br>已使用高置信度密钥模式审核全部 Git 引用，而不只审核当前工作树；未发现私钥、OpenAI、GitHub、AWS 或 Slack 凭据的疑似匹配。若后续发现真实凭据，请先撤销/轮换，再决定是否重写历史。
- [ ] If local history was rewritten, retain a tested local backup until all clones and the remote migration plan are confirmed. Do not merge or pull the old history into rewritten branches; any future remote update must be coordinated and use a protected `--force-with-lease` workflow, never `--mirror`.<br>若已在本地重写历史，请保留经验证的本地备份，直到全部克隆与远端迁移方案均已确认。不要把旧历史 merge 或 pull 回已改写分支；未来如需更新远端，必须协调后使用受保护的 `--force-with-lease` 流程，禁止使用 `--mirror`。
- [ ] Review public addresses, hostnames, usernames, internal project names, ticket links, and deployment topology. Remove or redact information that is not needed publicly.<br>审核公开地址、主机名、用户名、内部项目名称、工单链接和部署拓扑；不必要的信息应在发布前移除或脱敏。
- [ ] Confirm the public repository name, default branch, maintainer contact, and support boundary.<br>确认公开仓库名称、默认分支、维护者联系方式和支持边界。

## GitHub Settings / GitHub 设置

- [ ] Enable Actions and confirm this repository's CI passes for the first time.<br>启用 Actions，并确认本仓的 CI 首次运行通过。
- [ ] Enable Private vulnerability reporting and verify that the reporting path in [SECURITY.md](SECURITY.md) works.<br>启用 Private vulnerability reporting，并核对 [SECURITY.md](SECURITY.md) 的报告入口可用。
- [ ] Protect the default branch and require CI checks before merging.<br>为默认分支设置分支保护，至少要求 CI 检查通过后合并。
- [ ] Enable GitHub secret scanning, Dependabot alerts, and dependency updates when available.<br>启用 GitHub 的 secret scanning、Dependabot alerts 和依赖更新（可用时）。
- [ ] Create an issue-label, milestone, and discussion policy if the project needs community support.<br>创建 issue 标签、里程碑和讨论区策略（如项目需要社区支持）。

## Local Release Gates / 本地发布门禁

```powershell
git status --short
git diff --check
python -m ruff check codev_platform
python -m pytest tests/test_cli_parser.py tests/test_sync_hooks.py tests/test_resources_packaging.py -q
python -m codev_platform.cli --help
```

In an environment with a scanner installed, also scan all references and Git history for credentials. Scan output must not contain real credentials. If a possible leak is found, handle the credential itself before changing code or history.

在具备扫描工具的环境中，额外对所有引用和历史执行密钥扫描。扫描结果不能包含真实凭据；若发现疑似泄露，请先处理凭据本身，再处理代码和历史。

## Post-release Review / 发布后复核

- [ ] From a clean machine or temporary directory, follow the README for a basic installation and confirm that `codev-platform --help` works.<br>从干净机器或临时目录按 README 完成基础安装，并确认 `codev-platform --help` 可用。
- [ ] Review the public README, contribution guide, security policy, issue templates, and license links.<br>复核公开 README、贡献指南、安全政策、issue 模板和许可证链接。
- [ ] Check GitHub Actions, dependency alerts, and the vulnerability-reporting path.<br>检查 GitHub Actions、依赖告警和漏洞报告入口。
- [ ] Create the first release tag and release notes before announcing availability.<br>建立首个发布标签和变更说明后，再对外宣布可用性。
