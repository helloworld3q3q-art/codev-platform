// UnoCSS Webpack Plugin Wrapper for CommonJS
// 这个文件使用 CommonJS 格式，通过动态 import 加载 ESM 模块

let UnoCSS;

async function loadUnoCSS() {
  if (!UnoCSS) {
    const module = await import('@unocss/webpack');
    UnoCSS = module.default;
  }
  return UnoCSS;
}

// 导出一个函数，返回 Promise
module.exports = loadUnoCSS;
