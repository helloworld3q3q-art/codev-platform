"""CLI 子命令实现包 —— cli.py 按命令域拆分到此 (file-discipline §1, cli.py 瘦身)。

cli.py 只保留: 轻量命令 (init/current/list/register/validate/version/plugins/graph/web)
+ build_parser 注册 + main。重命令 (setup/config/sync/mcp 编排) 移到本包各模块,
cli.py re-export 保持 `codev_platform.cli.<symbol>` 向后兼容 (测试 / web repo 依赖)。
"""
