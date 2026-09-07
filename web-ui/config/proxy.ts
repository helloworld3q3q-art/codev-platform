import { resolveBackendOrigin } from './backendOrigin';

const target = resolveBackendOrigin();

export default {
  dev: {
    '/api/': {
      target,
      changeOrigin: true,
    },
  },
  test: {
    '/api/': {
      target,
      changeOrigin: true,
    },
  },
  pre: {
    '/api/': {
      target,
      changeOrigin: true,
    },
  },
};
