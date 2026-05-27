# 敏感信息与安全

| 项 | 处理方式 |
|---|---|
| 数据库密码 | 仅本地开发用默认值，生产必须 `${STOCK_DB_PASSWORD}` 环境变量 |
| API Token (Tushare 等) | 写进 `.env`（已 gitignore），禁止硬编码 |
| 用户数据 | 不入仓库；测试用 `seed_demo_data.py` 生成 |
| 推荐输出 | 必须含「不构成投资建议」「历史不代表未来」「投资有风险」三条免责（合规必填） |
| `/v3/api-docs` | 生产环境必须接入 basic auth 或反向代理鉴权 |

## 其他安全提示

- 不要硬编码生产密码（`application.yml` 默认值仅供本地开发）
- 不要把 token / API key 写进 git；用环境变量或 `.env`（已 `.gitignore`）
- Java `/v3/api-docs` 当前对外暴露，**生产部署时必须用 Knife4j basic auth 或反向代理鉴权**
- 风险提示文案不可省略；推荐结果输出必须包含免责声明（合规必填项）

---

## 已有 assertion 兜底

🟡 部分 assertion 化

已有：
- 测试：`python/stock-pipeline/tests/test_disclaimer_snapshot.py`（推荐三条免责必填校验）
- `.gitignore`：`.env` 已忽略（schema 层防泄密）
- Java：密码默认 `${STOCK_DB_PASSWORD:ewqdsa}` 环境变量优先（配置层防硬编码）

盲区（建议未来补）：
- 缺：CI 钩子扫"`.env` / `application-prod.yml` 含明文密码 / API key"（git secrets / detect-secrets）
- 缺：约定测试断言"前端展示推荐时回读 `disclaimerSnapshot` 而非硬编码"（已在 `snapshot-trio-write.md` §8 grep 自检列出，但无自动断言）
- 缺：生产环境检测 `/v3/api-docs` 是否裸暴露（部署后探测）
