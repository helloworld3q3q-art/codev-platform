const fs = require('fs');
const path = require('path');

// 配置
const config = {
  // 排除的目录
  excludeDirs: ['node_modules', 'dist', '.git', '.vscode', 'locales', 'utils'],
  // src目录下的目录命名规则（小写字母+数字）
  srcDirPattern: /^[a-z0-9]+$/,
  // components目录下的目录命名规则（大驼峰）
  componentsDirPattern: /^[A-Z][a-zA-Z0-9]+$/,
};

// 错误收集
const errors = [];

// 检查目录名是否符合规则
function checkDirectoryName(dirPath, parentDir) {
  const dirName = path.basename(dirPath);

  // 跳过排除的目录
  if (config.excludeDirs.includes(dirName)) {
    return;
  }

  // components目录下的目录需要使用大驼峰
  if (parentDir === 'components') {
    if (!config.componentsDirPattern.test(dirName)) {
      errors.push(`Error: Directory "${dirPath}" should use PascalCase (e.g., MyComponent)`);
    }
  }
  // src及其他目录下的目录需要使用小写字母+数字
  else {
    if (!config.srcDirPattern.test(dirName)) {
      errors.push(
        `Error: Directory "${dirPath}" should use lowercase letters and numbers only (e.g., mydir123)`,
      );
    }
  }
}

// 递归遍历目录
function traverseDirectory(currentPath, parentDir = '') {
  const items = fs.readdirSync(currentPath);

  for (const item of items) {
    const itemPath = path.join(currentPath, item);
    const stats = fs.statSync(itemPath);

    if (stats.isDirectory()) {
      // 检查目录名
      checkDirectoryName(itemPath, path.basename(currentPath));
      // 递归检查子目录
      traverseDirectory(itemPath, item);
    }
  }
}

// 主函数
function main() {
  const srcPath = path.join(process.cwd(), 'src');

  if (!fs.existsSync(srcPath)) {
    console.error('Error: src directory not found!');
    process.exit(1);
  }

  // 开始检查
  console.log('Checking directory names...');
  traverseDirectory(srcPath);

  // 输出结果
  if (errors.length > 0) {
    console.error('\nFound directory naming violations:');
    errors.forEach((error) => console.error(error));
    process.exit(1);
  } else {
    console.log('\nAll directory names are valid! ✨');
  }
}

main();
