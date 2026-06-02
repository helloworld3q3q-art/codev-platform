// Swagger 生成配置 —— 支持多 backend 并行生成
//
// 添加新 backend 时，往 sources 数组追加一项即可：
//   - name        : 子目录名 + 日志标识（'main' / 'codegraph' / ...）
//   - swaggerUrl  : 后端 /v3/api-docs 地址
//   - commonUrl   : 生成代码内拼到 fetch url 前面的前缀。空串=走前端 dev proxy。
//                   跨进程后端（如 codegraph-api 18082）必须填绝对 URL，绕过 proxy。
//   - namespace   : 生成的 typings.d.ts 用的 TS 命名空间。各 backend 必须唯一。
//   - outputSubDir: src/services/apis/ 下的子目录。空串=直接写到根（保持 main 向后兼容）。

export interface SwaggerSource {
  name: string;
  swaggerUrl: string;
  commonUrl: string;
  namespace: string;
  outputSubDir: string;
}

const sources: SwaggerSource[] = [
  {
    name: 'main',
    swaggerUrl: process.env.SWAGGER_URL || 'http://127.0.0.1:18088/openapi.json',
    commonUrl: '',
    namespace: 'API',
    outputSubDir: '',
  },
  {
    name: 'codegraph',
    swaggerUrl: process.env.CODEGRAPH_SWAGGER_URL || 'http://localhost:18082/v3/api-docs',
    commonUrl: 'http://localhost:18082',
    namespace: 'CG',
    outputSubDir: 'codegraph',
  },
];

// 向后兼容：default export 沿用旧字段（指向 main source）
const SwaggerAuth = {
  headers: {} as Record<string, string>,
  swaggerUrl: sources[0].swaggerUrl,
  commonUrl: sources[0].commonUrl,
  sources,
};

export default SwaggerAuth;
