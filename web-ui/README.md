# codev-platform 控制台 (web-ui)

codev-platform 管理后端的 Web 控制台。**整仓克隆自 `apps/stock-admin-web`** —— config / components / scripts / 主题 / 请求层 / 构建规范**逐字保留**,架构与样式一致;只删了业务页面,换成本平台自己的页面。

技术栈(同 stock-admin-web):React 18 + UmiJS Max + Ant Design Pro + antd 6 + antd-style + UnoCSS + axios。

## 与后端的关系

- 后端:`codev_platform/web`(FastAPI,默认 `:18088`,`python -m codev_platform.web.main`)。
- envelope 已对齐 `BaseApiResponse`(`result:0`+`errors[]`+`currentPage/total`)→ `src/utils/fetch` **逐字复用**。
- 枚举走后端真值源:`src/models/enum.ts` 运行时 `POST /api/v1/enums/list`,组件用 `useModel('enum')`。
- 开发代理:`config/proxy.ts` 把 `/api/*` 转发到 `:18088`。

## 运行(需你手动跑,AI 不代跑)

```bash
cd web-ui
pnpm install
pnpm dev          # 前端 (默认 :8000), 代理 /api -> :18088
# 另开终端起后端:  cd .. && python -m codev_platform.web.main
```

代码生成(后端在跑时):`pnpm run api`(拉 `:18088/openapi.json`)/ `pnpm run enums`(拉 `/api/v1/enums/list`)。

## 相对 stock-admin-web 的改动(仅这些,其余逐字保留)

| 项 | 改动 |
| --- | --- |
| `config/routes.ts` | 业务路由 → 本平台页面(projects/jobs/graph/enums + login/404) |
| `src/menus.tsx` `MENU_ITEMS` | 同上(其余 helper 保留) |
| `src/app.tsx` | `menu.request` 去掉业务 `postMenus`(改静态菜单);`/dashboard`→`/projects` redirect。其余架构不变 |
| `config/proxy.ts` | `/v1`→`:18081` 改为 `/api`→`:18088` |
| `src/models/enum.ts` | 枚举端点 `/api/v1/enums/list` |
| `scripts/swaggerauth.ts` | main swaggerUrl 指 `:18088/openapi.json` |
| `package.json` | name 改 codev-platform-admin-web;去 husky `prepare` |
| `src/pages/*` | 删全部业务页;新增 projects/jobs/graph/enums;login 改 passthrough |
| `src/components/StockDetailDrawer` | **删**(唯一依赖已删 stocks 页面、且无处引用的业务组件;其余 24 个组件全保留) |

## 已落地页面

| 页面                   | 路由          | 后端                                               |
| ---------------------- | ------------- | -------------------------------------------------- |
| 项目管理(ProTable)     | `/projects`   | `POST /api/v1/projects/list`                       |
| 任务中心               | `/jobs`       | `/api/v1/indexes/rebuild` + `/api/v1/jobs/detail`  |
| 图谱(force-graph-3d)   | `/graph`      | `POST /api/v1/graph/unified/graph`                 |
| 枚举元数据演示         | `/enums`      | `useModel('enum')` ← `/api/v1/enums/list`          |
| 登录(passthrough 占位) | `/user/login` | 写 localStorage(真 auth 波接 `/api/v1/auth/login`) |

## 待办(下一波)

- `fetch` 拦截器透传 `X-Project-Id`(多租户图谱接口需要;现 graph 页未带项目头)。
- 真登录接后端 Auth 波(`/api/v1/auth/login` 换 token,`getInitialState` 改请求 `/api/v1/auth/session`)。
- Orgs / Users 管理页(后端已就绪)。
- `pnpm run api` 接 openapi-typescript 生成 `src/services/apis/**`。

> 说明:`pnpm install` / `pnpm dev` / `tsc` 由人工跑验证(AI 不代执行依赖安装 / 长驻服务);前端代码无法 AI 自验,务必跑一遍 boot。
