import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';
import * as path from 'path';

// API返回的原始枚举项接口定义
interface RawEnumItem {
  enumType: string;
  enumValue: string;
  localLanguage: string;
  enumOrder: number;
  displayName: string;
  // 业务说明（可选），用于前端 tooltip 展示规则 / 指标的专业解读
  description?: string;
}

// 转换后的枚举项接口定义
interface EnumItem {
  value: string;
  label: string;
  order: number;
  description?: string;
}

// 枚举组接口定义
interface EnumGroup {
  [enumType: string]: EnumItem[];
}

// API响应接口定义
interface ApiResponse {
  result: number | string;
  message: string;
  data?: { [enumType: string]: RawEnumItem[] };
  errors?: any;
}

// 请求选项接口定义
interface RequestOptions {
  timeout?: number;
  headers?: Record<string, string>;
}

// 配置接口定义
interface Config {
  apiUrl: string;
  baseUrl: string;
  outputDir: string;
  outputFile: string;
  timeout: number;
  headers: Record<string, string>;
}

// 配置
const CONFIG: Config = {
  // codev-platform web backend，POST /api/v1/enums/list (passthrough 无需鉴权)。
  // 响应已对齐: {result:0, data:{enumType:[{enumValue,displayName,enumOrder,localLanguage,description}]}}。
  apiUrl: '/api/v1/enums/list',
  baseUrl: process.env.FETCH_ENUMS_BASE_URL || 'http://127.0.0.1:18088',

  // 输出配置
  outputDir: path.join(__dirname, '../src/models'),
  outputFile: 'enumslocal.tsx',

  // 请求配置
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
};

/**
 * 发起HTTP请求
 * @param url 请求URL
 * @param options 请求选项
 * @returns Promise<ApiResponse> 响应数据
 */
function makeRequest(url: string, options: RequestOptions = {}): Promise<ApiResponse> {
  return new Promise((resolve, reject) => {
    const isHttps = url.startsWith('https://');
    const client = isHttps ? https : http;
    // 本地接口使用 POST + 空 JSON body，与项目 API 约定一致。
    const body = '{}';
    const urlObj = new URL(url);
    const requestOptions = {
      hostname: urlObj.hostname,
      port: urlObj.port || (isHttps ? 443 : 80),
      path: urlObj.pathname + urlObj.search,
      method: 'POST',
      timeout: options.timeout ?? CONFIG.timeout,
      headers: {
        ...CONFIG.headers,
        'Content-Length': Buffer.byteLength(body),
      },
    };

    const req = client.request(requestOptions, (res) => {
      let data = '';

      res.on('data', (chunk: Buffer) => {
        data += chunk.toString();
      });

      res.on('end', () => {
        try {
          if (res.statusCode && res.statusCode >= 200 && res.statusCode < 300) {
            const jsonData: ApiResponse = JSON.parse(data);

            // 本地 CommonResult：result=0 表示成功，非 0 表示业务失败。
            const result = (jsonData as any).result;
            if (result !== 0 && result !== '0') {
              reject(new Error(`API错误: ${jsonData.message || `result=${result}`}`));
              return;
            }

            resolve(jsonData);
          } else {
            reject(new Error(`HTTP ${res.statusCode}: ${res.statusMessage}`));
          }
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : '未知错误';
          reject(new Error(`JSON解析失败: ${errorMessage}`));
        }
      });
    });

    req.on('error', (error: Error) => {
      reject(new Error(`请求失败: ${error.message}`));
    });

    req.on('timeout', () => {
      req.destroy();
      reject(new Error('请求超时'));
    });

    req.write(body);
    req.end();
  });
}

/**
 * 转换API返回的原始数据为标准格式
 * @param rawData API返回的原始数据
 * @returns 转换后的枚举数据
 */
function transformRawData(rawData: { [enumType: string]: RawEnumItem[] }): EnumGroup {
  const transformedData: EnumGroup = {};

  for (const [enumType, items] of Object.entries(rawData)) {
    // 确保枚举类型名称不包含冒号等特殊字符
    const cleanEnumType = enumType.replace(/[^a-zA-Z0-9_]/g, '');

    transformedData[cleanEnumType] = items
      .sort((a, b) => a.enumOrder - b.enumOrder) // 按order排序
      .map(
        (item: RawEnumItem): EnumItem => ({
          value: item.enumValue,
          label: item.displayName,
          order: item.enumOrder,
          // 优先用后端返回的业务说明；为空时回退到原有占位文案（保持向后兼容）
          description:
            item.description && item.description.length > 0
              ? item.description
              : `${item.enumType} - ${item.displayName}`,
        }),
      );
  }

  return transformedData;
}

/**
 * 获取枚举数据
 * @returns Promise<EnumGroup> 枚举数据
 */
async function fetchEnumsData(): Promise<EnumGroup> {
  const fullUrl = `${CONFIG.baseUrl}${CONFIG.apiUrl}`;

  console.log(`正在从 ${fullUrl} 获取枚举数据...`);

  try {
    const response = await makeRequest(fullUrl);

    // 检查响应数据是否存在
    if (response.data) {
      console.log('✅ 枚举数据获取成功');
      // 转换原始数据为标准格式
      return transformRawData(response.data);
    } else {
      throw new Error(`API返回错误: ${response.message || '未知错误'}`);
    }
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : '未知错误';
    console.error('❌ 获取枚举数据失败:', errorMessage);
    throw error;
  }
}

/**
 * 生成TSX文件内容
 * @param enumsData 枚举数据
 * @returns string TSX文件内容
 */
function generateTsxContent(enumsData: EnumGroup): string {
  const timestamp = new Date().toISOString();

  // 生成类型定义
  const typeDefinitions = `
// 枚举项类型定义
export interface EnumItem {
  value: string;
  label: string;
  order: number;
  description?: string;
}

// 枚举组类型定义
export interface EnumGroup {
  [enumType: string]: EnumItem[];
}
`;

  // 生成枚举数据 - 自定义格式化以避免键的引号
  const formatEnumData = (data: EnumGroup): string => {
    const entries = Object.entries(data).map(([key, items]) => {
      const formattedItems = items
        .map((item) => {
          return `    {
      value: "${item.value}",
      label: "${item.label}",
      order: ${item.order},
      description: "${item.description}"
    }`;
        })
        .join(',\n');

      return `  ${key}: [
${formattedItems}
  ]`;
    });

    return `{\n${entries.join(',\n')}\n}`;
  };

  const enumsDataString = formatEnumData(enumsData);

  const content = `/**
 * 枚举数据文件 - 自动生成，实际业务从接口获取，生成枚举文件只为展示，不做实际业务取值，或者编程取值
 * 注意：此文件仅用于展示枚举数据，不应该直接在业务代码中使用。
 * 提供给AI识别业务，用于生成业务代码。
 * 自动生成于: ${timestamp}
 * 请勿手动修改此文件
 */

${typeDefinitions}

// 枚举数据
const ENUMS_LOCAL: EnumGroup = ${enumsDataString};
export default ENUMS_LOCAL;
`;

  return content;
}

/**
 * 确保目录存在
 * @param dirPath 目录路径
 */
function ensureDirectoryExists(dirPath: string): void {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
    console.log(`📁 创建目录: ${dirPath}`);
  }
}

/**
 * 写入文件
 * @param filePath 文件路径
 * @param content 文件内容
 */
function writeFile(filePath: string, content: string): void {
  try {
    fs.writeFileSync(filePath, content, 'utf8');
    console.log(`✅ 文件写入成功: ${filePath}`);
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : '未知错误';
    console.error(`❌ 文件写入失败: ${errorMessage}`);
    throw error;
  }
}

/**
 * 统计枚举数据信息
 * @param enumsData 枚举数据
 * @returns 统计信息对象
 */
function getEnumsStatistics(enumsData: EnumGroup): {
  enumTypes: string[];
  totalItems: number;
} {
  const enumTypes = Object.keys(enumsData);
  const totalItems = Object.values(enumsData).reduce((sum, items) => sum + items.length, 0);

  return { enumTypes, totalItems };
}

/**
 * 显示帮助信息
 */
function showHelp(): void {
  console.log(`
枚举数据获取脚本 (TypeScript版本)

用法:
  npx ts-node fetch-enums.ts [选项]

选项:
  --help, -h          显示帮助信息
  --base-url <url>    设置API基础URL (默认: ${CONFIG.baseUrl})

环境变量:
  FETCH_ENUMS_BASE_URL  API基础URL

示例:
  npx ts-node fetch-enums.ts
  npx ts-node fetch-enums.ts --base-url https://api.example.com
  FETCH_ENUMS_BASE_URL=https://api.example.com npx ts-node fetch-enums.ts
`);
}

/**
 * 处理命令行参数
 */
function processCommandLineArgs(): void {
  // 处理帮助参数
  if (process.argv.includes('--help') || process.argv.includes('-h')) {
    showHelp();
    process.exit(0);
  }

  // 处理base-url参数
  const baseUrlIndex = process.argv.indexOf('--base-url');
  if (baseUrlIndex !== -1 && process.argv[baseUrlIndex + 1]) {
    CONFIG.baseUrl = process.argv[baseUrlIndex + 1];
  }
}

/**
 * 主函数
 */
async function main(): Promise<void> {
  try {
    console.log('🚀 开始获取枚举数据...');

    // 处理命令行参数
    processCommandLineArgs();

    // 获取枚举数据
    const enumsData = await fetchEnumsData();

    // 生成TSX内容
    console.log('📝 生成TSX文件内容...');
    const tsxContent = generateTsxContent(enumsData);

    // 确保输出目录存在
    ensureDirectoryExists(CONFIG.outputDir);

    // 写入文件
    const outputPath = path.join(CONFIG.outputDir, CONFIG.outputFile);
    writeFile(outputPath, tsxContent);

    // 统计信息
    const { enumTypes, totalItems } = getEnumsStatistics(enumsData);

    console.log('📊 统计信息:');
    console.log(`   - 枚举类型数量: ${enumTypes.length}`);
    console.log(`   - 枚举项总数: ${totalItems}`);
    console.log(`   - 输出文件: ${outputPath}`);

    console.log('🎉 枚举数据获取完成!');
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : '未知错误';
    console.error('💥 执行失败:', errorMessage);
    process.exit(1);
  }
}

// 导出模块
export { fetchEnumsData, generateTsxContent, CONFIG, EnumItem, EnumGroup, ApiResponse };

// 执行主函数
if (require.main === module) {
  main();
}
