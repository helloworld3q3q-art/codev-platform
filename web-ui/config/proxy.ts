export default {
  dev: {
    '/api/': {
      target: 'http://172.31.216.170:18088',
      changeOrigin: true,
    },
  },
  test: {
    '/api/': {
      target: 'http://172.31.216.170:18088',
      changeOrigin: true,
    },
  },
  pre: {
    '/api/': {
      target: 'http://172.31.216.170:18088',
      changeOrigin: true,
    },
  },
};
