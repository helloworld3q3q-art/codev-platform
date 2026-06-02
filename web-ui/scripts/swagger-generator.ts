/**
 * 从后端 OpenAPI 生成前端接口类型 (对齐 stock-admin-web 的 pnpm run api)。
 * 运行: pnpm run api (需后端 :18088 在跑)。
 *
 * FastAPI 暴露 /api/openapi.json (见 web/app.py)。第一版先拉下 schema 落盘,
 * 后续接 openapi-typescript / swagger-typescript-api 生成 src/services/apis/**。
 */
import axios from 'axios';
import { mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';

const BACKEND = process.env.CODEV_WEB_URL || 'http://127.0.0.1:18088';

async function main() {
  const { data } = await axios.get(`${BACKEND}/openapi.json`);
  const dir = join(__dirname, '../src/services');
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'openapi.json'), JSON.stringify(data, null, 2), 'utf-8');
  const paths = Object.keys(data?.paths ?? {}).length;
  console.log(`[api] fetched OpenAPI (${paths} paths) from ${BACKEND} -> src/services/openapi.json`);
  console.log('[api] TODO: 接 openapi-typescript 生成 typings.d.ts / services/apis (与 stock-admin-web 对齐)');
}

main().catch((e) => {
  console.error('[api] failed:', e?.message || e);
  process.exit(1);
});
