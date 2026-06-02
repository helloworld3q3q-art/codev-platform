// Swagger/OpenAPI 生成配置 —— 支持多 backend 并行生成
//
// codev-platform 单后端 (FastAPI :18088, /openapi.json 是 OpenAPI 3.x)。图谱接口已并入
// 本后端 /api/v1/graph/*, 不再需要独立 codegraph-api source。
//   - swaggerUrl  : 后端 OpenAPI JSON 地址 (FastAPI 默认 /openapi.json)。
//   - commonUrl   : 生成代码内拼到 fetch url 前面的前缀。空串=走前端 dev proxy(/api -> :18088)。
//   - namespace   : 生成的 typings.d.ts 用的 TS 命名空间。
//   - outputSubDir: src/services/apis/ 下的子目录。空串=直接写到根。

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
];

// 向后兼容：default export 沿用旧字段（指向 main source）
const SwaggerAuth = {
  headers: {} as Record<string, string>,
  swaggerUrl: sources[0].swaggerUrl,
  commonUrl: sources[0].commonUrl,
  sources,
};

export default SwaggerAuth;
