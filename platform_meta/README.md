# Local project registry / 本地项目登记

`platform_meta/projects/` is intentionally empty in the public repository.
The platform supports multiple projects, but live registrations can contain organization-specific identifiers, repository locations, and operational paths. Keep them on each operator's machine and never commit them.

`platform_meta/projects/` 在公开仓库中刻意保持为空。平台支持多项目，但实际登记可能包含组织专属标识、仓库位置和运行路径；请仅保存在各自机器上，禁止提交。

## Local setup / 本地配置

1. Use the onboarding command to create or register a local project.
2. Keep the generated `meta.json` under `platform_meta/projects/` only on the local machine.
3. Review `git status` before every commit. The repository ignores all live entries in this directory.

1. 使用 onboarding 命令创建或登记本地项目。
2. 生成的 `meta.json` 仅保存在本机的 `platform_meta/projects/` 下。
3. 每次提交前检查 `git status`；仓库会忽略该目录中的所有实际登记。
