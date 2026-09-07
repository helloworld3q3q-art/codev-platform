"""codev-platform: 多项目 AI 工具栈基础设施 (本地原型, server 部署待启)。

模块组织:
    codev_platform.core.project_id   project_id resolver (env / .claude/project.json / HTTP header)
    codev_platform.core.paths        chroma / graph / codegraph 路径约定
    codev_platform.cli               CLI 入口 (init / current / list-projects / validate)

业务项目接入: pip install -e <path-to-codev-platform>; 仓根加 .claude/project.json
详细架构: docs/plans/team-deploy-2026-05-27-design.md
"""

__version__ = "0.1.0"
