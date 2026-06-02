#!/usr/bin/env ts-node

import { execSync } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';
import * as path from 'path';
import SwaggerAuth from './swaggerauth';

/**
 * 参照 swagger 格式的 API 生成器
 * 生成符合项目规范的 TypeScript 服务文件
 */

interface SwaggerConfig {
  swaggerUrl: string;
  outputDir: string;
  baseTypesImport: string;
  namespace: string;
  commonUrl: string;
}

interface ApiMethod {
  methodName: string;
  paramType: string | null;
  returnType: string;
  summary: string;
  method: string;
  interface: string | null;
}

interface ServiceData {
  interfaces: Map<string, string>;
  methods: ApiMethod[];
}

interface SwaggerSpec {
  openapi: string;
  info: {
    title: string;
    version: string;
  };
  servers: Array<{
    url: string;
    description: string;
  }>;
  paths: Record<string, Record<string, any>>;
  components?: {
    schemas?: Record<string, any>;
  };
}

interface Schema {
  type?: string;
  $ref?: string;
  properties?: Record<string, any>;
  required?: string[];
  description?: string;
  items?: Schema;
  enum?: string[];
  format?: string;
  additionalProperties?: Schema;
}

interface Parameter {
  name: string;
  in: string;
  required?: boolean;
  schema?: Schema;
  description?: string;
}

interface RequestBody {
  content?: Record<
    string,
    {
      schema?: Schema;
    }
  >;
  required?: boolean;
}

interface Operation {
  tags?: string[];
  summary?: string;
  description?: string;
  operationId?: string;
  parameters?: Parameter[];
  requestBody?: RequestBody;
  responses?: Record<
    string,
    {
      description: string;
      content?: Record<
        string,
        {
          schema?: Schema;
        }
      >;
    }
  >;
}

class SwaggerStyleGenerator {
  private config: SwaggerConfig;

  constructor(config?: Partial<SwaggerConfig>) {
    this.config = {
      namespace: 'API',
      // JUSDA API 文档地址
      swaggerUrl: SwaggerAuth.swaggerUrl,
      // 生成的服务文件输出目录
      outputDir: path.join(__dirname, '../src/services/apis'),
      // 基础响应类型导入
      baseTypesImport:
        "import {\n  BasePaginationResponse,\n  downloadFileByPost,\n  get,\n  post,\n  put,\n} from '@/utils/fetch';",
      // 生成代码中拼到 fetch url 前面的前缀（空串=走前端 proxy）
      commonUrl: SwaggerAuth.commonUrl,
      ...config,
    };
  }

  /**
   * 从 URL 获取 Swagger JSON
   */
  async fetchSwaggerFromUrl(url: string): Promise<SwaggerSpec> {
    return new Promise((resolve, reject) => {
      const client = url.startsWith('https:') ? https : http;

      client
        .get(
          url,
          {
            headers: SwaggerAuth.headers,
          },
          (res) => {
            let data = '';
            res.on('data', (chunk) => (data += chunk));
            res.on('end', () => {
              try {
                const parsed = JSON.parse(data);
                // 自动解包 CommonResult 包装：stock-admin-api 的全局响应拦截器会把
                // /v3/api-docs 也包成 {result, message, data, errors, ok}。
                // codegraph-api 等独立模块未走拦截器，直接返回 OpenAPI 规范。
                // 两种格式都要兼容。
                if (parsed && typeof parsed === 'object' && !parsed.paths && parsed.data?.paths) {
                  resolve(parsed.data);
                } else {
                  resolve(parsed);
                }
              } catch (error) {
                debugger;
                reject(
                  new Error(
                    `解析 Swagger JSON 失败: ${error instanceof Error ? error.message : '未知错误'}`,
                  ),
                );
              }
            });
          },
        )
        .on('error', reject);
    });
  }

  /**
   * 生成 TypeScript 接口类型
   */
  generateTypeScriptInterface(name: string, schema: Schema, description: string = ''): string {
    if (!schema || !schema.properties) {
      const comment = description || `${name} 接口`;
      return `// ${comment}\ninterface ${name} {\n  [key: string]: any;\n}`;
    }

    // 生成接口注释
    let interfaceComment = description;
    if (!interfaceComment) {
      // 根据接口名称生成默认注释
      if (name.includes('Request')) {
        interfaceComment = `${name} 请求参数`;
      } else if (name.includes('Response')) {
        interfaceComment = `${name} 响应数据`;
      } else if (name.includes('DTO')) {
        interfaceComment = `${name} 数据传输对象`;
      } else if (name.includes('Param')) {
        interfaceComment = `${name} 参数`;
      } else {
        interfaceComment = `${name} 接口`;
      }
    }

    let content = `// ${interfaceComment}\ninterface ${name} {\n`;

    Object.entries(schema.properties).forEach(([propName, propSchema]) => {
      const isRequired = schema.required && schema.required.includes(propName);
      const optional = isRequired ? '' : '?';
      const type = this.convertSwaggerTypeToTS(propSchema);
      const comment = propSchema.description ? ` // ${propSchema.description}` : '';

      content += `  ${propName}${optional}: ${type};${comment}\n`;
    });

    content += '}';
    return content;
  }

  /**
   * 转换 Swagger 类型到 TypeScript 类型
   */
  convertSwaggerTypeToTS(schema: Schema): string {
    if (!schema) return 'any';

    if (schema.$ref) {
      const refType = schema.$ref.split('/').pop() || 'any';
      return refType;
    }

    switch (schema.type) {
      case 'string':
        if (schema.enum) {
          return `'${schema.enum.join("' | '")}'`;
        }
        if (schema.format === 'date-time') {
          return 'string'; // 格式: yyyy-MM-dd HH:mm:ss
        }
        return 'string';
      case 'number':
      case 'integer':
        return 'number';
      case 'boolean':
        return 'boolean';
      case 'array':
        const itemType = this.convertSwaggerTypeToTS(schema.items || {});
        return `${itemType}[]`;
      case 'object':
        if (schema.additionalProperties) {
          const valueType = this.convertSwaggerTypeToTS(schema.additionalProperties);
          return `Record<string, ${valueType}>`;
        }
        return 'Record<string, any>';
      default:
        return 'any';
    }
  }

  /**
   * 生成 API 方法
   */
  generateApiMethod(path: string, method: string, operation: Operation): ApiMethod {
    const operationId = operation.operationId || this.generateOperationId(path, method);
    const methodName = this.generateSemanticMethodName(path, method, operation);
    const summary = operation.summary || operation.description || methodName;

    // 获取参数类型（从 typings.d.ts 中）
    const parameters = operation.parameters || [];
    const requestBody = operation.requestBody;

    let paramType: string | null = null;
    let hasParams = false;

    if (parameters.length > 0 || requestBody) {
      hasParams = true;
      // 从请求体中获取类型名称
      if (requestBody && requestBody.content) {
        const schema = requestBody.content['application/json']?.schema;
        if (schema && schema.$ref) {
          paramType = schema.$ref.split('/').pop() || null;
        }
      }
      // 如果没有请求体类型但有查询参数，生成参数接口名称
      if (!paramType && parameters.length > 0) {
        paramType = `${this.toPascalCase(methodName)}Params`;
      }
      // 如果都没有，使用默认参数类型
      if (!paramType) {
        paramType = 'any';
      }
    }

    // 生成返回类型
    const responseSchema =
      operation.responses?.['200']?.content?.['*/*']?.schema ||
      operation.responses?.['200']?.content?.['application/json']?.schema;
    const returnType = responseSchema ? this.convertSwaggerTypeToTS(responseSchema) : 'any';

    // 生成参数接口
    let interfaceContent = null;
    let finalParamType = paramType;

    // 生成方法体
    const methodBody = this.generateMethodBody(
      path,
      method,
      hasParams,
      returnType,
      operationId,
      finalParamType || undefined, // 将 null 转换为 undefined
    );
    const namespace = this.config.namespace;

    // 获取路径的最后一部分用于判断
    const pathParts = path.split('/').filter((part) => part && !part.startsWith('{'));
    const lastPart = pathParts[pathParts.length - 1] || '';

    // 对于下载方法，使用 Promise<void>
    const methodReturnType = lastPart.startsWith('download')
      ? 'Promise<void>'
      : `Promise<${namespace}.${returnType}>`;

    if (hasParams && parameters.length > 0 && !requestBody) {
      const interfaceResult = this.generateParamInterface(
        paramType!,
        parameters,
        requestBody,
        path,
      );
      interfaceContent = interfaceResult;

      // 从接口内容中提取实际使用的接口名称
      const interfaceNameMatch = interfaceResult.match(/interface\s+([\w]+)\s+\{/);
      if (interfaceNameMatch && interfaceNameMatch[1]) {
        finalParamType = interfaceNameMatch[1];
      }
    }

    return {
      methodName,
      paramType: hasParams ? finalParamType : null,
      returnType,
      summary,
      method: `// ${summary}\nexport async function ${methodName}(${hasParams ? `data: Partial<${namespace}.${finalParamType}>` : ''}): ${methodReturnType} {\n${methodBody}\n}`,
      interface: interfaceContent,
    };
  }

  /**
   * 生成参数接口
   */
  generateParamInterface(
    interfaceName: string,
    parameters: Parameter[],
    requestBody?: RequestBody,
    path?: string, // 添加路径参数
  ): string {
    // 使用路径信息生成唯一的接口名称
    let uniqueInterfaceName = interfaceName;
    if (path) {
      // 从路径中提取关键信息
      const pathParts = path.split('/').filter((part) => part && !part.startsWith('{'));

      // 如果有多个路径部分，使用倒数第二部分来区分接口名称
      if (pathParts.length > 1) {
        const secondLastPart = pathParts[pathParts.length - 2] || '';
        if (secondLastPart && !uniqueInterfaceName.includes(this.toPascalCase(secondLastPart))) {
          uniqueInterfaceName = `${this.toPascalCase(secondLastPart)}${uniqueInterfaceName}`;
        }
      }
    }

    // 生成参数接口注释
    let interfaceComment = `${uniqueInterfaceName} 请求参数`;
    if (uniqueInterfaceName.includes('Params')) {
      interfaceComment = `${uniqueInterfaceName} 查询参数`;
    } else if (uniqueInterfaceName.includes('Request')) {
      interfaceComment = `${uniqueInterfaceName} 请求参数`;
    }

    let content = `// ${interfaceComment}\ninterface ${uniqueInterfaceName} {\n`;

    // 处理路径参数和查询参数
    parameters.forEach((param) => {
      const optional = param.required ? '' : '?';
      const type = this.convertSwaggerTypeToTS(param.schema || { type: 'string' });
      const comment = param.description ? ` // ${param.description}` : '';
      content += `  ${param.name}${optional}: ${type};${comment}\n`;
    });

    // 处理请求体（如果有的话）
    if (requestBody && requestBody.content) {
      const schema = requestBody.content['application/json']?.schema;
      if (schema && schema.properties) {
        Object.entries(schema.properties).forEach(([propName, propSchema]) => {
          const isRequired = schema.required && schema.required.includes(propName);
          const optional = isRequired ? '' : '?';
          const type = this.convertSwaggerTypeToTS(propSchema);
          const comment = propSchema.description ? ` // ${propSchema.description}` : '';
          content += `  ${propName}${optional}: ${type};${comment}\n`;
        });
      }
    }

    content += '}';
    return content;
  }

  /**
   * 生成方法体
   */
  generateMethodBody(
    path: string,
    method: string,
    hasParams: boolean,
    returnType: string,
    operationId: string,
    paramType?: string, // 添加参数类型
  ): string {
    const urlPath = path.replace(/{([^}]+)}/g, '${data.$1}');
    const namespace = this.config.namespace;
    // 关键：这里的 commonUrl 前加反斜杠，防止 node 运行时提前解析
    const urlTpl = '`' + '\${commonUrl}' + urlPath + '`';

    // 获取路径的最后一部分
    const pathParts = path.split('/').filter((part) => part && !part.startsWith('{'));
    const lastPart = pathParts[pathParts.length - 1] || '';

    if (lastPart.startsWith('download')) {
      // Handle file download endpoint without return type parameter
      return hasParams
        ? `  return await downloadFileByPost({\n    url: ${urlTpl},\n    data,\n  });`
        : `  return await downloadFileByPost({\n    url: ${urlTpl},\n    data: {},\n  });`;
    }

    if (lastPart.startsWith('upload')) {
      // Handle file upload endpoint
      return hasParams
        ? `  return await uploadFiles<${namespace}.${returnType}>({\n    url: ${urlTpl},\n    files: data.files || [],\n    data,\n  });`
        : `  return await uploadFiles<${namespace}.${returnType}>({\n    url: ${urlTpl},\n    files: [],\n    data: {},\n  });`;
    }

    if (method.toLowerCase() === 'get') {
      return hasParams
        ? `  return await get<${namespace}.${returnType}>({\n    url: ${urlTpl},\n    data,\n  });`
        : `  return await get<${namespace}.${returnType}>({\n    url: ${urlTpl},\n  });`;
    } else if (method.toLowerCase() === 'put') {
      return hasParams
        ? `  return await put<${namespace}.${returnType}>({\n    url: ${urlTpl},\n    data,\n  });`
        : `  return await put<${namespace}.${returnType}>({\n    url: ${urlTpl},\n    data: {},\n  });`;
    } else if (method.toLowerCase() === 'delete') {
      return hasParams
        ? `  return await dele<${namespace}.${returnType}>({\n    url: ${urlTpl},\n    data,\n  });`
        : `  return await dele<${namespace}.${returnType}>({\n    url: ${urlTpl},\n    data: {},\n  });`;
    } else {
      return hasParams
        ? `  return await post<${namespace}.${returnType}>({\n    url: ${urlTpl},\n    data,\n  });`
        : `  return await post<${namespace}.${returnType}>({\n    url: ${urlTpl},\n    data: {},\n  });`;
    }
  }

  /**
   * 生成语义化的方法名称
   */
  generateSemanticMethodName(path: string, method: string, operation: Operation): string {
    // 从路径中提取关键信息
    const pathParts = path.split('/').filter((part) => part && !part.startsWith('{'));
    const lastPart = pathParts[pathParts.length - 1] || '';
    const secondLastPart = pathParts[pathParts.length - 2] || '';

    // 根据 HTTP 方法生成基础名称
    let methodName = '';
    const httpMethod = method.toLowerCase();

    // 如果最后一部分小于等于4个字符，且倒数第二部分存在，则合并使用
    let namePart = lastPart;
    if (
      (lastPart.length <= 4 || lastPart.startsWith('download') || lastPart.startsWith('upload')) &&
      secondLastPart &&
      secondLastPart !== lastPart
    ) {
      namePart = `${secondLastPart}${this.toPascalCase(lastPart)}`;
    }

    if (httpMethod === 'get') {
      methodName = `get${this.toPascalCase(namePart)}`;
    } else if (httpMethod === 'post') {
      methodName = `post${this.toPascalCase(namePart)}`;
    } else if (httpMethod === 'put') {
      methodName = `put${this.toPascalCase(namePart)}`;
    } else if (httpMethod === 'delete') {
      methodName = `delete${this.toPascalCase(namePart)}`;
    } else {
      methodName = `${httpMethod}${this.toPascalCase(namePart)}`;
    }

    return methodName;
  }

  /**
   * 动态生成导入语句
   */
  generateDynamicImports(methods: ApiMethod[]): string {
    const usedMethods = new Set<string>();

    // 分析每个方法使用的 HTTP 方法
    methods.forEach((method) => {
      if (method.method.includes('get<')) {
        usedMethods.add('get');
      }
      if (method.method.includes('post<')) {
        usedMethods.add('post');
      }
      if (method.method.includes('put<')) {
        usedMethods.add('put');
      }
      if (method.method.includes('dele<')) {
        usedMethods.add('dele');
      }
      // 检查是否包含下载方法
      if (method.method.includes('downloadFileByPost')) {
        usedMethods.add('downloadFileByPost');
      }
      // 检查是否包含上传方法
      if (method.method.includes('uploadFiles')) {
        usedMethods.add('uploadFiles');
      }
    });

    // 构建导入语句 (只导出方法体实际用到的;分页返回类型走 API.PageResult_* 命名空间,
    // 不需要 import BasePaginationResponse —— 旧逻辑会导致生成文件 no-unused-vars 报错)
    const imports: string[] = [];

    // 添加使用的 HTTP 方法
    if (usedMethods.has('get')) imports.push('get');
    if (usedMethods.has('post')) imports.push('post');
    if (usedMethods.has('put')) imports.push('put');
    if (usedMethods.has('dele')) imports.push('dele');
    if (usedMethods.has('downloadFileByPost')) imports.push('downloadFileByPost');
    if (usedMethods.has('uploadFiles')) imports.push('uploadFiles');

    // 如果没有任何导入，返回默认导入语句
    if (imports.length === 0) {
      return `import { post } from '@/utils/fetch';`;
    }

    return `import {\n  ${imports.join(',\n  ')},\n} from '@/utils/fetch';`;
  }

  /**
   * 解决方法名称冲突
   */
  resolveMethodNameConflicts(methods: ApiMethod[]): void {
    const methodNames = new Map<string, number>();

    methods.forEach((method) => {
      const baseName = method.methodName;
      if (methodNames.has(baseName)) {
        const count = methodNames.get(baseName)! + 1;
        methodNames.set(baseName, count);
        method.methodName = `${baseName}${count}`;

        // 更新方法体中的方法名
        method.method = method.method.replace(
          new RegExp(`export async function ${baseName}\\(`, 'g'),
          `export async function ${method.methodName}(`,
        );
      } else {
        methodNames.set(baseName, 1);
      }
    });
  }

  /**
   * 生成操作 ID
   */
  generateOperationId(path: string, method: string): string {
    const cleanPath = path.replace(/[{}]/g, '').replace(/\//g, '_').replace(/^_/, '');
    return `${method}_${cleanPath}`;
  }

  /**
   * 转换为驼峰命名
   */
  toCamelCase(str: string): string {
    return str.replace(/[-_](.)/g, (_, char) => char.toUpperCase());
  }

  /**
   * 转换为帕斯卡命名
   */
  toPascalCase(str: string): string {
    const camelCase = this.toCamelCase(str);
    return camelCase.charAt(0).toUpperCase() + camelCase.slice(1);
  }

  /**
   * 生成所有类型定义
   */
  generateAllTypes(swaggerSpec: SwaggerSpec): string {
    const types: string[] = [];
    const schemas = swaggerSpec.components?.schemas || {};

    // 添加 any 类型定义
    types.push('// any 类型定义\ntype any = any;');

    Object.entries(schemas).forEach(([name, schema]) => {
      const description = schema.description || '';
      // 去掉 export，使其为全局类型声明
      let typeContent = this.generateTypeScriptInterface(name, schema, description);
      typeContent = typeContent.replace(/^export\s+/gm, '');
      types.push(typeContent);
    });
    return types.join('\n\n');
  }

  /**
   * 按标签分组生成服务文件
   */
  async generateServicesByTags(swaggerSpec: SwaggerSpec): Promise<Map<string, string>> {
    const servicesByTag: Record<string, ServiceData> = {};
    const allParamInterfaces = new Map<string, string>(); // 收集所有参数接口

    // 遍历所有路径和方法
    Object.entries(swaggerSpec.paths).forEach(([path, pathMethods]) => {
      Object.entries(pathMethods).forEach(([method, operation]) => {
        if (typeof operation !== 'object' || !operation.tags) return;

        let tag = operation.tags[0] || 'default';
        if (tag.includes('-')) {
          tag = tag.split('-')[0];
        }
        if (!servicesByTag[tag]) {
          servicesByTag[tag] = {
            interfaces: new Map(),
            methods: [],
          };
        }

        const apiMethod = this.generateApiMethod(path, method, operation);
        servicesByTag[tag].methods.push(apiMethod);

        // 收集需要的接口类型
        if (apiMethod.paramType && apiMethod.interface) {
          servicesByTag[tag].interfaces.set(apiMethod.paramType, apiMethod.interface);
          allParamInterfaces.set(apiMethod.paramType, apiMethod.interface);
        }
      });
    });

    // 处理每个 tag 中的方法名称冲突
    for (const [tag, service] of Object.entries(servicesByTag)) {
      this.resolveMethodNameConflicts(service.methods);
    }

    // 为每个标签生成服务文件
    for (const [tag, service] of Object.entries(servicesByTag)) {
      await this.generateServiceFile(tag, service, swaggerSpec);
    }

    // 生成索引文件
    await this.generateIndexFile(Object.keys(servicesByTag));

    // 返回收集到的所有参数接口，用于更新 typings.d.ts
    return allParamInterfaces;
  }

  /**
   * 生成单个服务文件
   */
  async generateServiceFile(
    tag: string,
    service: ServiceData,
    swaggerSpec: SwaggerSpec,
  ): Promise<void> {
    // 将中文标签名映射为英文文件名
    const englishFileName = this.getEnglishFileName(tag);
    const fileName = `${englishFileName.toLocaleLowerCase()}.ts`;
    const filePath = path.join(this.config.outputDir, fileName);

    // 确保输出目录存在
    if (!fs.existsSync(this.config.outputDir)) {
      fs.mkdirSync(this.config.outputDir, { recursive: true });
    }

    // 动态生成导入语句
    const imports = this.generateDynamicImports(service.methods);
    let content = `${imports}\n\n`;

    // 添加 commonUrl（从实例配置读，支持多 backend 各自的前缀）
    content += `const commonUrl = '${this.config.commonUrl}';\n\n`;

    // 生成 API 方法
    service.methods.forEach((method) => {
      content += method.method + '\n\n';
    });

    fs.writeFileSync(filePath, content, 'utf8');
    console.log(`✅ 生成服务文件: ${fileName}`);
  }

  /**
   * 获取文件名
   */
  getEnglishFileName(tag: string): string {
    // 直接使用英文标签名，进行简单的清理处理
    let cleanName = tag.replace(/[^\w\-]/g, '');

    if (!cleanName) {
      cleanName = 'api';
    }

    // 确保首字母是小写
    cleanName = cleanName.charAt(0).toLowerCase() + cleanName.slice(1);

    // 如果以数字开头，添加前缀
    if (/^\d/.test(cleanName)) {
      cleanName = 'api' + cleanName;
    }

    // 如果清理后的名称是空的或只包含特殊字符，使用默认名称
    if (!cleanName || cleanName === '') {
      cleanName = 'api';
    }

    return cleanName;
  }

  /**
   * 生成索引文件
   */
  async generateIndexFile(tags: string[]): Promise<void> {
    const indexPath = path.join(this.config.outputDir, 'index.ts');

    let content = `// API 生成时间：${new Date().toISOString()}\n\n`;

    // 去重并生成导入
    const uniqueTags = [...new Set(tags)];
    uniqueTags.forEach((tag) => {
      const englishFileName = this.getEnglishFileName(tag);
      content += `import * as ${englishFileName} from './${englishFileName.toLowerCase()}';\n`;
    });

    content += '\nexport {\n';
    uniqueTags.forEach((tag) => {
      const englishFileName = this.getEnglishFileName(tag);
      content += `  ${englishFileName},\n`;
    });
    content += '};\n\n';

    // 导出默认对象
    content += 'export default {\n';
    uniqueTags.forEach((tag) => {
      const englishFileName = this.getEnglishFileName(tag);
      content += `  ${englishFileName},\n`;
    });
    content += '};\n';

    fs.writeFileSync(indexPath, content, 'utf8');
    console.log(`✅ 生成索引文件: index.ts`);
  }

  /**
   * 生成类型定义文件
   */
  async generateTypesFile(swaggerSpec: SwaggerSpec): Promise<void> {
    const typesPath = path.join(this.config.outputDir, 'typings.d.ts');

    // 确保输出目录存在
    if (!fs.existsSync(this.config.outputDir)) {
      fs.mkdirSync(this.config.outputDir, { recursive: true });
    }

    const typesContent = this.generateAllTypes(swaggerSpec);
    fs.writeFileSync(typesPath, typesContent, 'utf8');
    console.log(`✅ 生成类型定义文件: typings.d.ts`);
  }

  /**
   * 更新类型定义文件，添加参数接口
   */
  async updateTypingsFile(
    swaggerSpec: SwaggerSpec,
    paramInterfaces: Map<string, string>,
  ): Promise<void> {
    const typesPath = path.join(this.config.outputDir, 'typings.d.ts');

    // 读取现有的类型定义
    let existingContent = '';
    if (fs.existsSync(typesPath)) {
      existingContent = fs.readFileSync(typesPath, 'utf8');
    }

    // 生成参数接口内容
    const paramInterfacesContent = Array.from(paramInterfaces.values()).join('\n\n');
    const namespace = this.config.namespace;
    // 合并内容
    const updatedContent =
      existingContent + (paramInterfacesContent ? '\n\n' + paramInterfacesContent : '');
    const _updatedContent = `declare namespace ${namespace} {
      \n  ${updatedContent};
    }`;
    // 写回文件
    fs.writeFileSync(typesPath, _updatedContent, 'utf8');
    console.log(`✅ 更新类型定义文件: typings.d.ts (添加了 ${paramInterfaces.size} 个参数接口)`);
  }

  /**
   * 从提供的规范数据生成
   */
  async generateFromSpec(swaggerSpec: SwaggerSpec): Promise<void> {
    try {
      console.log('🚀 开始从提供的 OpenAPI 规范生成 API 服务...');

      if (!swaggerSpec || !swaggerSpec.paths) {
        throw new Error('无效的 OpenAPI 规范');
      }

      console.log(`📋 找到 ${Object.keys(swaggerSpec.paths).length} 个 API 端点`);

      // 生成基础类型定义文件
      await this.generateTypesFile(swaggerSpec);

      // 生成服务文件并收集参数接口
      const paramInterfaces = await this.generateServicesByTags(swaggerSpec);

      // 更新 typings.d.ts 文件，添加参数接口
      await this.updateTypingsFile(swaggerSpec, paramInterfaces);

      console.log('🎉 API 服务生成完成！');
    } catch (error) {
      console.error('❌ 生成失败:', error instanceof Error ? error.message : '未知错误');
      process.exit(1);
    }
  }

  /**
   * 主要的生成方法
   */
  async generate(): Promise<void> {
    try {
      console.log('🚀 开始生成符合 swagger 格式的 API 服务...');
      let swaggerSpec: SwaggerSpec;
      // 从在线 API 获取
      console.log('📡 尝试从在线 API 获取 Swagger 规范...');
      console.log(`🔗 API 地址: ${this.config.swaggerUrl}`);
      swaggerSpec = await this.fetchSwaggerFromUrl(this.config.swaggerUrl);
      console.log('✅ 成功从在线 API 获取 Swagger 规范');

      if (!swaggerSpec || !swaggerSpec.paths) {
        throw new Error('无效的 Swagger 规范');
      }

      console.log(`📋 找到 ${Object.keys(swaggerSpec.paths).length} 个 API 端点`);

      // 先生成基础类型定义文件
      await this.generateTypesFile(swaggerSpec);

      // 再生成服务文件并收集参数接口
      const paramInterfaces = await this.generateServicesByTags(swaggerSpec);

      // 更新 typings.d.ts 文件，添加参数接口
      await this.updateTypingsFile(swaggerSpec, paramInterfaces);

      console.log('🎉 API 服务生成完成！');

      const stdout = execSync('npm run lint:prettierapi');
      console.log(`🎉 API prettier完成！${stdout}`);
    } catch (error) {
      console.error('❌ 生成失败:', error instanceof Error ? error.message : '未知错误');
      process.exit(1);
    }
  }
}

// 如果直接运行此脚本：循环跑 swaggerauth.sources 里每一个 backend
if (require.main === module) {
  if (process.argv.includes('--from-spec')) {
    console.log('请使用 generateFromSpec 方法并提供规范数据');
  } else {
    (async () => {
      const sources = SwaggerAuth.sources;
      // 支持 --only=name 指定只跑某个 backend（如 --only=codegraph）
      const onlyArg = process.argv.find((a) => a.startsWith('--only='));
      const onlyName = onlyArg ? onlyArg.split('=')[1] : null;
      const targets = onlyName ? sources.filter((s) => s.name === onlyName) : sources;
      if (targets.length === 0) {
        console.error(`❌ 找不到名为 "${onlyName}" 的 source`);
        process.exit(1);
      }
      for (const source of targets) {
        console.log(`\n${'='.repeat(60)}`);
        console.log(`▶ 生成 backend: ${source.name}`);
        console.log(`  swagger:  ${source.swaggerUrl}`);
        console.log(`  output:   src/services/apis/${source.outputSubDir || ''}`);
        console.log(`  namespace: ${source.namespace}`);
        console.log(`${'='.repeat(60)}\n`);
        const generator = new SwaggerStyleGenerator({
          swaggerUrl: source.swaggerUrl,
          outputDir: path.join(__dirname, '../src/services/apis', source.outputSubDir),
          namespace: source.namespace,
          commonUrl: source.commonUrl,
        });
        await generator.generate();
      }
    })().catch((err) => {
      console.error('❌ 多 backend 生成失败:', err);
      process.exit(1);
    });
  }
}

export default SwaggerStyleGenerator;
