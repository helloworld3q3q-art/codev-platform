# platform-meta

AI 协作工具栈跨项目共享元数据仓(原型阶段单仓内嵌占位,未来抽独立仓 / server 部署)。

## 目录结构

```
platform-meta/
├── README.md              # 本文件
├── platform/              # 跨项目通用层(当前为空, 待从 .claude/rules/ 抽取)
│   ├── rules/             # 跨项目工程纪律(workflow / commit / windows-powershell ...)
│   ├── memory/            # 跨项目用户偏好(语言 / commit 风格 ...)
│   └── skills/            # 跨项目通用 skill
└── projects/              # 各 project 注册表
    └── openclaw-stock/    # 当前量化项目
        └── meta.json      # project 元数据
```

## 设计原则

- **platform/** 是跨项目复用层, 任何加进来的内容必须真有 ≥ 2 个项目用到, 不预先填充
- **projects/<id>/** 只放元数据(display_name / owner / created_at), 业务 rules 仍在各仓的 `.claude/rules/`
- 当前原型阶段 platform/ 为空, 随实际跨项目需求 organic 长出

## 迁移路径

本地原型 → server 部署时:
1. 整个 `platform-meta/` 目录搬到 server 仓库 (或独立 git repo)
2. 各业务仓通过 git submodule / MCP 拉取 platform/ 内容
3. server 维护 projects/ 注册表 + token 颁发

参考: `docs/plans/roadmap-2026-05-27/team-deploy-2026-05-27-design.md`
