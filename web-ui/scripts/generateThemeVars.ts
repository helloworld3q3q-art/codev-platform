import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { resolve } from 'path';

// 由于项目中没有 less-vars-to-js 依赖，我们将自己实现解析功能
function lessToJS(lessContent: string): Record<string, string> {
  const variables: Record<string, string> = {};
  const variableRegex = /@([a-zA-Z0-9_-]+)\s*:\s*([^;]+);/g;
  let match;

  while ((match = variableRegex.exec(lessContent)) !== null) {
    // 移除注释
    let value = match[2].trim();
    const commentIndex = value.indexOf('/*');
    if (commentIndex !== -1) {
      value = value.substring(0, commentIndex).trim();
    }
    variables[match[1]] = value;
  }

  return variables;
}

function generateThemeVars(): void {
  try {
    // 读取 Less 文件内容
    const lessFilePath = resolve(__dirname, '../src/styles/variables.less');
    const lessContent = readFileSync(lessFilePath, 'utf8');

    // 解析 Less 变量
    const themeVariables = lessToJS(lessContent);

    // 确保输出目录存在
    const outputPath = resolve(__dirname, '../src/styles');
    if (!existsSync(outputPath)) {
      mkdirSync(outputPath, { recursive: true });
    }

    // 将变量写入 TypeScript 文件
    const tsOutputPath = resolve(outputPath, 'theme.ts');
    const tsContent = `export default ${JSON.stringify(themeVariables, null, 2)};\n`;
    writeFileSync(tsOutputPath, tsContent);

    console.log('小可爱已启动');
  } catch (error) {
    console.error('Error generating theme variables:', error);
    process.exit(1);
  }
}

// 执行生成
generateThemeVars();
