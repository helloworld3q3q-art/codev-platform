# Security Policy / 安全政策

## Supported Scope / 支持范围

The project is pre-release. Maintainers address security issues only on the current development line; historical snapshots and unreleased versions are not supported security-maintenance lines.

项目仍处于预发布阶段。维护者只会在当前开发线处理安全问题；历史快照和未发布版本不构成受支持的安全维护线。

## Reporting a Vulnerability / 报告漏洞

Do not disclose a potential vulnerability, exploit details, credentials, or user data in a public issue, discussion, pull request, or log.

请不要在公开 issue、讨论区、PR 或日志中披露潜在漏洞、利用细节、凭据或用户数据。

Before public release, the project owner should enable **Private vulnerability reporting** in GitHub. Once enabled, use **Report a vulnerability** from the repository Security page. If it is temporarily unavailable, contact a maintainer through a private GitHub channel and provide only minimally redacted information.

公开发布前，项目所有者应在 GitHub 仓库中启用 **Private vulnerability reporting**。启用后，请使用仓库 Security 页面中的 **Report a vulnerability** 提交报告；若该入口暂不可用，请通过维护者的私有 GitHub 联系渠道报告，并仅提供经过脱敏的最小信息。

Include the following in a report:

报告应包括：

- Affected component, version, or commit / 受影响的组件、版本或提交；
- Reproduction steps and expected impact / 可复现步骤和预期影响；
- Whether credentials, authorization bypass, data exposure, or remote code execution is involved / 是否涉及凭据、权限绕过、数据暴露或远程执行；
- A proposed mitigation, if available / 建议的缓解措施（如有）。

Maintainers will acknowledge the report first and then coordinate remediation and disclosure. Do not publish exploit details before a fix is released.

维护者会先确认收到报告，再与报告者协调修复和披露方式。请避免在修复发布前公开利用细节。

## Configuration and Credentials / 配置与凭据

- Never commit API keys, tokens, passwords, DSNs, private keys, or real user data.<br>不要提交 API key、token、密码、DSN、私钥或真实用户数据。
- Use environment variables or a local `.env` file excluded by `.gitignore` for sensitive configuration.<br>使用环境变量或被 `.gitignore` 排除的本地 `.env` 文件保存敏感配置。
- If a credential may have leaked, revoke or rotate it immediately and consider rewriting public history. Removing text from the working tree alone does not remove a historical leak.<br>若怀疑凭据已泄露，请立即撤销或轮换凭据，并在需要时重写公开历史；仅删除工作树中的文本并不能消除历史泄露。
