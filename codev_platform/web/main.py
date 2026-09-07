"""Web Backend uvicorn 入口 (plan §二) —— `python -m codev_platform.web.main`。

prod 启动前由 gateway.deploy_policy_error fail-fast (auth_mode 必须 token), 与 agent 服务一致。
"""
from __future__ import annotations


def main() -> None:
    import uvicorn

    from codev_platform.core.config import load_config
    from codev_platform.web.config import web_host, web_port, web_tls

    cfg = load_config()
    host = web_host(cfg)
    port = web_port(cfg)
    # 方案 A: 配了 web.tls_cert + web.tls_key 则 uvicorn 直接终结 TLS (https); 否则 HTTP (反代终结 TLS)。
    tls = web_tls(cfg)
    ssl_kwargs = {"ssl_certfile": tls[0], "ssl_keyfile": tls[1]} if tls else {}
    uvicorn.run(
        "codev_platform.web.app:create_runtime_app",
        host=host,
        port=port,
        factory=True,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()
