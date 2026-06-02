"""Web Backend uvicorn 入口 (plan §二) —— `python -m codev_platform.web.main`。

prod 启动前由 gateway.deploy_policy_error fail-fast (auth_mode 必须 token), 与 agent 服务一致。
"""
from __future__ import annotations


def main() -> None:
    import uvicorn

    from codev_platform.core.config import load_config
    from codev_platform.web.config import web_host, web_port

    cfg = load_config()
    host = web_host(cfg)
    port = web_port(cfg)
    uvicorn.run("codev_platform.web.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
