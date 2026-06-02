// 开发代理: 前端 /api/* 转发到 codev-platform web backend (默认 :18088, 见 web/config.py)。
// 与 stock-admin-web 同构 (它代理 /v1/ -> :18081), 这里代理 /api/ -> :18088。
export default {
  dev: {
    '/api/': {
      target: 'http://127.0.0.1:18088',
      changeOrigin: true,
    },
  },
  test: {
    '/api/': {
      target: 'http://127.0.0.1:18088',
      changeOrigin: true,
    },
  },
};
