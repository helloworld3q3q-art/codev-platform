# codev-platform

> This document is bilingual: English and Simplified Chinese have equal status.
>
> 本文档为中英双语，英文与简体中文具有同等效力。

AI collaboration infrastructure for multiple projects. It provides a shared `project_id` namespace plus document retrieval, code graphs, cross-layer impact analysis, layered memory, and local operations tooling for independent repositories.

面向多项目协作的 AI 工具栈基础设施。它为不同代码仓提供统一的 `project_id` 命名空间，以及文档检索、代码图谱、跨层影响分析、分层记忆和本地运维能力。

> Status: pre-release. Interfaces, commands, and deployment paths may change. Validate in an isolated development environment before connecting a production project.
>
> 状态：预发布。接口、命令和部署方式仍可能调整；请先在隔离的开发环境中验证，再接入生产项目。

## Capabilities / 能力概览

| Capability / 能力 | Description / 说明 |
| --- | --- |
| Project isolation / 项目隔离 | Manages configuration, data, and metadata ownership through `project_id`.<br>以 `project_id` 管理多项目配置、数据和元数据归属。 |
| Documentation and code understanding / 文档与代码理解 | Provides Chroma document retrieval, CodeGraph symbol/call relationships, and a unified graph.<br>提供 Chroma 文档检索、CodeGraph 符号/调用关系与统一图谱能力。 |
| Impact analysis / 影响分析 | Traces cross-layer impact through APIs, tables, pages, and call paths.<br>支持按接口、表、页面和调用链追踪跨层影响。 |
| Collaboration memory / 协作记忆 | Supports organization-, team-, project-, and personal-level Agent Memory.<br>提供组织、团队、项目与个人层级的 Agent Memory。 |
| Operations entry points / 运维入口 | The CLI covers onboarding, indexing, MCP services, health checks, backups, and runtime management.<br>CLI 覆盖接入、索引、MCP 服务、健康检查、备份与运行时管理。 |

## Repository Layout / 仓库结构

| Path / 路径 | Description / 说明 |
| --- | --- |
| `codev_platform/` | Python package, CLI, MCP services, graphs, Agent, Web API, and operations modules.<br>Python 包、CLI、MCP 服务、图谱、Agent、Web API 与运维模块。 |
| `web-ui/` | React administration console.<br>React 管理控制台。 |
| `platform_meta/` | Project registration and metadata.<br>项目登记与元数据。 |
| `docs/` | Architecture, operations, audits, and development records.<br>架构、运维、审计与开发记录。 |
| `tests/` | Automated tests.<br>自动化测试。 |

## Quick Start / 快速开始

Python 3.10 or later is required. The following steps install lightweight development dependencies and verify the CLI. Read the configuration and resource requirements before running full retrieval or Agent capabilities.

需要 Python 3.10 或更高版本。以下步骤安装轻量开发依赖并验证 CLI；运行完整检索或 Agent 能力前，请先阅读配置和资源要求。

```powershell
git clone <repository-url>
Set-Location codev-platform

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

codev-platform --help
```

For a first local setup, inspect the environment first and then let the CLI create the local runtime when appropriate:

首次搭建本地工具栈时，先查看环境诊断，再按需让 CLI 创建本地运行环境：

```powershell
codev-platform setup --dry-run
codev-platform setup --auto
```

The full runtime downloads additional dependencies and may use local models, databases, and index directories. Do not commit machine-level configuration or runtime data. See [`config.example.json`](config.example.json) for example fields.

完整运行时会下载额外依赖，并可能使用本地模型、数据库和索引目录。这些机器级配置与运行数据不应提交到 Git；示例字段见 [`config.example.json`](config.example.json)。

## Common Commands / 常用命令

```powershell
# View all commands and help / 查看所有命令与帮助
codev-platform --help

# Manage a project identifier and local configuration / 管理项目标识与本地配置
codev-platform init <project-id>
codev-platform config doctor --redact

# Onboard a business repository (writes local configuration in the target repository)
# 接入一个业务仓库（会写入目标仓的本地配置）
codev-platform onboard <project-id> --repo <path-to-repository> --no-index

# Check local MCP service status / 查看本地 MCP 服务状态
codev-platform serve-mcp status

# Run focused tests / 执行目标测试
python -m pytest tests/test_cli_parser.py tests/test_sync_hooks.py tests/test_resources_packaging.py
python -m ruff check codev_platform
```

Before running a command that writes configuration, indexes, or a business repository, read its `--help` output and the project constraints in [`AGENTS.md`](AGENTS.md).

执行会写入配置、索引或业务仓的命令前，请先阅读对应的 `--help` 与 [`AGENTS.md`](AGENTS.md) 中的项目约束。

## Contributing / 参与贡献

Reproducible issues and improvements are welcome. Read the [contribution guide](CONTRIBUTING.md) before contributing. For security issues, follow the [security policy](SECURITY.md) and do not disclose vulnerabilities or sensitive data in a public issue.

欢迎提交可复现的问题与改进建议。贡献前请阅读[贡献指南](CONTRIBUTING.md)；安全问题请遵循[安全政策](SECURITY.md)，不要通过公开 issue 披露漏洞或敏感信息。

## Open-source License / 开源许可

`codev-platform` is licensed under the [Apache License 2.0](LICENSE). Attribution notices are in [NOTICE](NOTICE). This license covers project-owned source and documentation; third-party dependencies and separately licensed components retain their own license terms. Contributions are accepted under the terms described in the [contribution guide](CONTRIBUTING.md).

`codev-platform` 采用 [Apache License 2.0](LICENSE) 开源，署名通知见 [NOTICE](NOTICE)。该许可证适用于项目自行拥有的源代码与文档；第三方依赖和单独许可的组件继续遵循各自的许可证。贡献的授权规则见[贡献指南](CONTRIBUTING.md)。其余发布门禁见[开源发布检查清单](RELEASING.md)。
