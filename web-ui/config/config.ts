import { defineConfig } from '@umijs/max';
import { join } from 'path';
import defaultSettings from './defaultSettings';
import proxy from './proxy';
import routes from './routes';

const { REACT_APP_ENV = 'dev' } = process.env;

// 与 stock-admin-web config/config.ts 同构 (精简: 去业务专属 splitChunks/keepalive/unocss)。
export default defineConfig({
  alias: { '@': join(__dirname, '../src') },
  antd: {},
  access: {},
  model: {}, // 启用 useModel (src/models/*.ts), enum.ts 据此提供 useModel('enum')
  initialState: {},
  request: {},
  layout: { locale: false, ...defaultSettings },
  proxy: proxy[REACT_APP_ENV as keyof typeof proxy],
  routes,
  title: 'codev-platform 控制台',
  theme: { 'root-entry-name': 'variable' },
  hash: true,
  fastRefresh: true,
  mfsu: { strategy: 'normal' },
  npmClient: 'pnpm',
});
