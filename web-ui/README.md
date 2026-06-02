# codev-platform 控制台 (web-ui)

codev-platform 管理后端的 Web 控制台。**风格对齐 `apps/stock-admin-web`**(React 18 + UmiJS Max + Ant Design Pro + antd-style + axios),请求层 / enum model / 构建规范逐字复用。

## 与后端的关系

- 后端: `codev_platform/web`(FastAPI,默认 `:18088`,`python -m codev_platform.web.main`)。
- envelope 已对齐 stock-admin-web 的 `BaseApiResponse`(`result:0`+`errors[]`+`currentPage/total`)→ `src/utils/fetch` **逐字复用**,无需改请求层。
- 枚举走后端真值源:`src/models/enum.ts` 运行时 `POST /api/v1/enums/list`,组件用 `useModel('enum').getEnumOptions/getFormattedEnums`(禁前端硬编码业务枚举)。
- 开发代理:`config/proxy.ts` 把前端 `/api/*` 转发到 `:18088`。

## 运行(需你手动跑,AI 不代跑)

```bash
cd web-ui
pnpm install          # 或 npm install
pnpm dev              # 起前端 (默认 :8000), 代理 /api -> :18088
# 另开一个终端起后端:
#   cd .. && python -m codev_platform.web.main
```

代码生成(后端在跑时):

```bash
pnpm run enums        # 拉 /api/v1/enums/list -> src/models/enumslocal.tsx (离线兜底, 非必需)
pnpm run api          # 拉 /openapi.json -> src/services/openapi.json (后续接 openapi-typescript)
```

## 已落地页面(第一批)

| 页面 | 路由 | 对接后端 |
|---|---|---|
| 项目管理(ProTable) | `/projects` | `POST /api/v1/projects/list` |
| 任务中心(提交/查状态) | `/jobs` | `/api/v1/indexes/rebuild` + `/api/v1/jobs/detail` |
| 图谱可视化(force-graph) | `/graph` | `POST /api/v1/graph/cross-link/graph` |
| 枚举元数据演示 | `/enums` | `useModel('enum')` ← `/api/v1/enums/list` |
| 登录(passthrough 占位) | `/user/login` | 写 localStorage(真 auth 波接 `/api/v1/auth/login`) |

## 待办(下一波)

- **`fetch` 透传 `X-Project-Id`**:多租户接口(graph 等)需带项目头。建议在 `src/utils/fetch/fetch.ts` 的请求拦截器里从全局态注入 `X-Project-Id`(当前 graph 页用固定 `codev-platform` 占位)。
- **真登录**:Auth 波后端就绪后,登录改调 `POST /api/v1/auth/login` 换 token,`getInitialState` 改请求 `/api/v1/auth/session`。
- **Orgs/Users 管理页**:等后端 Auth/Orgs/Users 波。
- **OpenAPI typings**:`pnpm run api` 接 openapi-typescript 生成 `src/services/apis/**`(对齐 stock-admin-web)。

> 说明:本控制台为脚手架起步版,`pnpm install` / `pnpm dev` 由人工跑验证(AI 不代执行依赖安装/长驻服务)。
> 共享业务组件(StockChartDrawer 等)未从 stock-admin-web 搬运 —— 只复用与业务无关的基础设施。
