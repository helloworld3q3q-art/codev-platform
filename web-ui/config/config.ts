import { defineConfig } from '@umijs/max';
import { join } from 'path';
import defaultSettings from './defaultSettings';
import proxy from './proxy';
import routes from './routes';
import { configureSplitChunks } from './splitChunksConfig';
import { keepalivePaths } from './utils';

const { REACT_APP_ENV = 'dev', SYSTEM_MAC = '' } = process.env;

export default defineConfig({
  chainWebpack: SYSTEM_MAC === '' ? configureSplitChunks : undefined,
  alias: {
    '@': join(__dirname, '../src'),
  },
  // 配置图片等静态资源处理
  inlineLimit: 10000,
  // 禁用 sourcemap 减小体积
  devtool: REACT_APP_ENV === 'dev' ? 'source-map' : false,
  antd: {},
  access: {},
  fastRefresh: true,
  hash: true,
  // Umi 构建会检查 esbuild helper 冲突，开启 IIFE 后可避免异步 chunk helper 命名冲突。
  esbuildMinifyIIFE: true,
  initialState: {},
  layout: {
    locale: false,
    ...defaultSettings,
  },
  model: {},
  moment2dayjs: {
    preset: 'antd',
    plugins: ['duration'],
  },
  proxy: proxy[REACT_APP_ENV as keyof typeof proxy],
  request: {},
  routes,
  title: 'OpenClaw Stock',
  theme: {
    'root-entry-name': 'variable',
  },
  ignoreMomentLocale: true,
  mock: false,
  //================ pro 插件配置 =================
  presets: ['umi-presets-pro'],
  // @ts-ignore - provided by @alita/plugins via umi-presets-pro
  keepalive: keepalivePaths,
  mfsu: {
    strategy: 'normal',
    exclude: ['uno.css'],
  },
  requestRecord: {},
});
