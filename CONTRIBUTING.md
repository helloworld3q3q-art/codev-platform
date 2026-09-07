# Contributing / 贡献指南

Thank you for considering a contribution to `codev-platform`.

感谢你考虑为 `codev-platform` 做贡献。

## Reporting Issues / 提交问题

- Use GitHub issues for reproducible defects, unexpected behavior, and improvement proposals. Search existing issues before opening a new one.<br>GitHub issue 用于提交可复现的缺陷、异常行为和改进建议。提交前请先搜索现有 issue，避免重复。
- Include the environment, minimal reproduction steps, expected behavior, and actual behavior. Remove tokens, passwords, user data, and internal addresses from logs.<br>请提供运行环境、最小复现步骤、期望行为和实际行为；日志必须移除 token、密码、用户数据和内部地址。
- Do not report security issues publicly; use the process in [SECURITY.md](SECURITY.md).<br>安全问题不要公开提交，改按 [SECURITY.md](SECURITY.md) 的流程报告。

## Local Development / 本地开发

Python 3.10 or later is required. An isolated virtual environment is recommended:

本项目要求 Python 3.10 或更高版本。建议使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Full runtime, Web API, and model-related features need additional dependencies and local services. Start from the target command's `--help` output, and do not add machine-level configuration or index data to a commit.

完整运行时、Web API 和模型相关功能还需要额外依赖与本地服务。请先从目标命令的 `--help` 开始，避免将机器级配置或索引数据纳入提交。

## Pull Requests / 提交拉取请求

1. Keep each pull request focused on one reviewable problem and explain its motivation and impact.<br>每个 PR 应聚焦一个可审查的问题，并说明动机与影响范围。
2. Add or update tests for behavior changes; update related commands, configuration, or links when documentation changes.<br>行为变化需要补充或更新测试；文档变化应同步修正相关命令、配置或链接。
3. Run validation matching the affected area before submission. For example:<br>提交前请至少运行与改动范围匹配的验证，例如：

   ```powershell
   python -m ruff check codev_platform
   python -m pytest tests/test_cli_parser.py tests/test_sync_hooks.py tests/test_resources_packaging.py
   ```

4. Do not commit `.env` files, access tokens, passwords, private addresses, `data/`, local indexes, build artifacts, or dependency directories.<br>不提交 `.env`、访问令牌、密码、私有地址、`data/`、本地索引、构建产物或依赖目录。
5. `web-ui/src/services/**` is generated or semi-generated API code. Prefer the project's generation command rather than editing it manually.<br>`web-ui/src/services/**` 属于生成或半生成 API 层；如需更新，优先使用项目生成命令而不是手工修改。

[`AGENTS.md`](AGENTS.md) records repository-level development constraints, directory rules, and more complete validation guidance.

项目中的 [`AGENTS.md`](AGENTS.md) 记录了仓库级开发约束、目录规则和更完整的验证口径。

## Contribution License / 贡献授权

The project uses the [Apache License 2.0](LICENSE) as its outbound license. By submitting a pull request, patch, or other contribution for inclusion, you represent that you have the right to grant it and agree that it may be distributed under Apache-2.0. This follows Section 5 of the project license; no separate CLA is required at this stage.

本项目以 [Apache License 2.0](LICENSE) 作为对外许可证。提交 PR、补丁或其他拟纳入项目的贡献，即表示你确认自己有权授予该贡献，并同意它可按 Apache-2.0 发布。这与项目许可证第 5 节保持一致；目前不要求单独签署 CLA。

Do not submit code, documentation, assets, model configuration, or data that you do not own or lack permission to relicense. If material needs different terms, clearly mark it as **Not a Contribution** and contact a maintainer before submitting it.

不要提交你不拥有、或无权再许可的代码、文档、素材、模型配置或数据。若材料必须使用不同条款，请明确标记为 **Not a Contribution**，并在提交前联系维护者。
