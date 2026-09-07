import * as fs from 'fs';
import * as path from 'path';

interface CompatFile {
  path: string;
  content: string;
}

/**
 * 创建 Ant Design v6 兼容性文件
 * 解决 UmiJS MFSU 功能和 Layout 插件对 antd 样式文件的依赖问题
 */
function createAntdCompatFile(): void {
  const compatFiles: CompatFile[] = [
    {
      // MFSU 依赖的 antd.less 文件
      path: path.resolve(__dirname, '../node_modules/antd/dist/antd.less'),
      content: `/* 
 * Ant Design v6 兼容性文件 - MFSU 支持
 * 
 * 这个文件是为了兼容 UmiJS MFSU 功能而创建的
 * Ant Design v6 已经移除了 Less 文件，改为使用 CSS-in-JS
 * 
 * 导入实际的 CSS 文件
 */

@import './antd.css';
`,
    },
    {
      // Layout 插件依赖的 themes/default.less 文件
      path: path.resolve(__dirname, '../node_modules/antd/es/style/themes/default.less'),
      content: `/* 
 * Ant Design v6 兼容性文件 - Layout 插件支持
 * 
 * 这个文件是为了兼容 UmiJS Layout 插件而创建的
 * Ant Design v6 已经移除了 Less 文件，改为使用 CSS-in-JS
 * 
 * 在 v6 中，主题配置应该通过 ConfigProvider 的 theme 属性来设置
 */

/* 基础颜色变量 - 这些会被 ConfigProvider 的主题配置覆盖 */
:root {
  --ant-primary-color: #1890ff;
  --ant-success-color: #52c41a;
  --ant-warning-color: #faad14;
  --ant-error-color: #f5222d;
  --ant-info-color: #1890ff;
}
`,
    },
  ];

  let createdCount = 0;
  let existingCount = 0;

  compatFiles.forEach(({ path: filePath, content }) => {
    const dir = path.dirname(filePath);

    // 检查父目录是否存在，如果不存在则创建
    if (!fs.existsSync(dir)) {
      try {
        fs.mkdirSync(dir, { recursive: true });
        console.log(`📁 创建目录: ${dir}`);
      } catch (error) {
        console.error(`❌ 创建目录失败 ${dir}:`, (error as Error).message);
        return;
      }
    }

    // 检查文件是否已存在
    if (fs.existsSync(filePath)) {
      console.log(`✅ 兼容性文件已存在: ${path.relative(process.cwd(), filePath)}`);
      existingCount++;
      return;
    }

    try {
      // 写入兼容性文件
      fs.writeFileSync(filePath, content, 'utf8');
      console.log(`✅ 成功创建兼容性文件: ${path.relative(process.cwd(), filePath)}`);
      createdCount++;
    } catch (error) {
      console.error(`❌ 创建兼容性文件失败 ${filePath}:`, (error as Error).message);
    }
  });

  console.log(`\n📊 兼容性文件处理完成: 创建 ${createdCount} 个，已存在 ${existingCount} 个`);
}

// 如果直接运行此脚本
if (require.main === module) {
  createAntdCompatFile();
}

export default createAntdCompatFile;
