// 自定义 loader：以二进制模式读取文件，不做任何转换
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

module.exports = function rawFileLoader(content) {
  // 确保以二进制模式处理
  this.cacheable && this.cacheable();

  const callback = this.callback;
  const resourcePath = this.resourcePath;

  // 读取原始文件（二进制模式）
  const source = fs.readFileSync(resourcePath);

  // 计算 hash
  const hash = crypto.createHash('md5').update(source).digest('hex').substring(0, 8);

  // 获取文件信息
  const ext = path.extname(resourcePath);
  const name = path.basename(resourcePath, ext);
  const outputPath = `static/${name}.${hash}${ext}`;

  // 输出文件
  this.emitFile(outputPath, source, null);

  // 返回文件路径
  callback(null, `module.exports = __webpack_public_path__ + ${JSON.stringify(outputPath)};`);
};

// 关键：设置为二进制模式
module.exports.raw = true;
