// eslint-disable-next-line @typescript-eslint/no-var-requires
const loadUnoCSS = require('./utils/unocssWebpackPlugin');
const path = require('path');

// 定义分包配置函数
export const configureSplitChunks = async (config: any, { webpack }: { webpack: any }) => {
  // 动态加载 UnoCSS webpack 插件
  const UnoCSS = await loadUnoCSS();
  config.plugin('unocss').use(UnoCSS());

  // 设置 webpack 的 chunks 配置
  config.output.set('chunkFilename', '[name].[chunkhash:8].js');

  // 使用自定义 loader 以二进制模式处理文件，确保不重新编码
  config.module
    .rule('raw-binary-assets')
    .test(/\.(png|jpe?g|gif|webp|ico|mp4|webm|ogg|mp3|wav|flac|aac)$/i)
    .type('javascript/auto')
    .use('raw-file-loader')
    .loader(path.resolve(__dirname, 'utils/rawFileLoader.js'));

  // SVG 单独处理（因为它是文本文件，不需要二进制模式）
  // config.module
  //   .rule('svg-assets')
  //   .test(/\.svg$/i)
  //   .type('asset/resource')
  //   .set('generator', {
  //     filename: 'static/[name].[contenthash:8][ext]',
  //   });

  // 简化分包配置 - 确保稳定加载
  // config.optimization.splitChunks({
  //   chunks: 'all',
  //   automaticNameDelimiter: '.',
  //   minSize: 20000,
  //   maxSize: 500000,
  //   minChunks: 1,
  //   maxAsyncRequests: 10,
  //   maxInitialRequests: 8,
  //   cacheGroups: {
  //     // 1. 默认配置 - 保持简单
  //     default: false,
  //     defaultVendors: false,

  //     // 2. React 核心 - 基础依赖
  //     react: {
  //       name: 'react',
  //       test: /[\/]node_modules[\/](react|react-dom)[\/]/,
  //       priority: 20,
  //       chunks: 'all',
  //       reuseExistingChunk: true,
  //     },

  //     // 3. Ant Design
  //     antd: {
  //       name: 'antd',
  //       test: /[\/]node_modules[\/](@ant-design|antd|rc-)[\/]/,
  //       priority: 15,
  //       chunks: 'all',
  //       reuseExistingChunk: true,
  //     },

  //     // 4. 第三方库
  //     vendor: {
  //       name: 'vendor',
  //       test: /[\/]node_modules[\/]/,
  //       priority: 10,
  //       chunks: 'all',
  //       reuseExistingChunk: true,
  //       minSize: 30000,
  //     },

  //     // 5. 公共代码
  //     common: {
  //       name: 'common',
  //       minChunks: 2,
  //       priority: 5,
  //       chunks: 'all',
  //       reuseExistingChunk: true,
  //       minSize: 20000,
  //     },
  //   },
  // });
};
