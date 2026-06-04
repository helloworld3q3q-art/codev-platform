# API 服务规范

## 一、API 自动生成

### 生成命令

```bash
pnpm run api
```

### 生成结果

文件位于 `src/services/apis/`:

- `{tagname}api.ts` - API 服务文件（每个 Swagger tag 一个）
- `typings.d.ts` - 类型定义（API 命名空间）
- `index.ts` - 导出索引

### 配置文件

- `scripts/swaggerauth.ts` - Swagger URL 和认证配置
- `scripts/swagger-generator.ts` - 生成器主逻辑

## 二、使用生成的 API

### 基础用法

```typescript
// ✅ 导入 API 方法
import { getUser, createUser, deleteUser } from '@/services/apis/userapi';

// ✅ 使用 API 命名空间类型
const [user, setUser] = useState<API.UserInfo>();

// ✅ 调用 API（带错误处理）
try {
  const user = await getUser({ userId: '123' });
  setUser(user.data);
} catch (error) {
  // 错误已自动处理和显示
}
```

## 三、HTTP 请求封装

### 核心特性

1. **请求去重** - 1 秒内相同请求自动合并
2. **统一错误处理** - 集中处理错误消息和登录跳转
3. **Token 自动刷新** - 401 响应时自动刷新 token

### 请求方法

```typescript
import { get, post, put, dele, uploadFile, downloadFileByPost } from '@/utils/fetch';

// 基础请求
const data = await get<ApiResponse<DataType>>({ url: '/api/user', data: { id: '123' } });
const result = await post<ApiResponse<ResultType>>({ url: '/api/user', data: { name: 'test' } });

// 文件上传（带进度）
const result = await uploadFile({
  url: '/api/upload',
  file,
  onProgress: (percent) => setProgress(percent),
});

// 文件下载
await downloadFileByPost({
  url: '/api/export',
  data: { ids: selectedRowKeys },
});
```

### 响应格式

```typescript
interface BaseApiResponse<T = any> {
  result?: number | string; // 200 表示成功
  message?: string;
  data?: T;
  errors?: Array<{ errorCode?: string; errorMessage?: string; field?: string }>;
}

interface BasePaginationResponse<T> extends BaseApiResponse<T[]> {
  currentPage?: number;
  pageSize?: number;
  total?: number;
}
```

### 错误处理

**自动处理**：

- 401 未授权 → 刷新 token 或跳转登录
- 网络错误 → 显示"网络请求超时"
- 业务错误 → 显示 `message` 或 `errors[0].errorMessage`

**手动处理**：

```typescript
try {
  await deleteUser({ id: '123' });
  message.success('删除成功');
  actionRef.current?.reload();
} catch (error) {
  // 错误已显示，做额外清理工作
  setSelectedRows([]);
}
```

## 四、注意事项

1. **禁止手动修改生成的代码** - 会被下次生成覆盖
2. **使用生成的类型定义** - 避免使用 `any`
3. **API 调用需 try-catch** - 可能失败，需要处理错误
4. **请求会自动合并** - 相同请求在 1 秒内只会发送一次
5. **错误消息会自动显示** - 不需要重复调用 `message.error`
6. **重新生成 API** - 后端接口变更后运行 `pnpm run api`

---

## 五、后端 schema 红线（影响 `pnpm run api` 生成,踩过两次)

写后端 FastAPI 接口 / Pydantic schema 时,以下两条直接决定前端能不能用,**违反会生成坏代码或静默失效**:

### 5.1 🚨 schema 的 docstring / 字段 description 必须**单行**

多行 docstring/description 会进 OpenAPI `description`,前端 swagger 生成器把它原样塞进
`typings.d.ts` 的 `//` 注释 —— **换行处破坏 `.d.ts` 语法**(`tsc` 报 `TS1127 Invalid character`,整个 typings 解析失败)。

```python
# ❌ 多行 docstring → 生成的 typings.d.ts 报错
class FooRequest(BaseModel):
    """第一行说明。
    第二行说明。"""        # ← 换行进 description → 破坏 .d.ts

# ✅ 单行 docstring;要写多行用 # 注释(# 注释不进 OpenAPI)
class FooRequest(BaseModel):
    """一句话单行说明。"""
    # 详细多行说明用普通 # 注释, 不会进 description。
```

参考既有处理:`codev_platform/web/schemas/audit.py` 的 `AuditListRequest`、`core/httpkit/pagination.py` 的 `PageBody`。

### 5.2 POST 列表接口的分页 / 过滤参数走 **body**,不要用 `Query`

前端 `post()`(`utils/fetch`)**一律把参数发 body**(沿 Java `@RequestBody` 约定),不发 query。
后端 list 接口若用 `code: str = Query(...)` / `Depends(page_params)`(query 版)取参:

- 必填 query 参数(如 `code`)→ 前端 body 传不到 → **400**;
- 可选分页 → 前端 `pageNumber/pageSize` 传不到 → **静默永远第 1 页**(数据少时不易发现)。

✅ 正确:POST 列表接口收 **body 模型**;分页继承 `core.httpkit.pagination.PageBody`(`pageNumber/pageSize/offset`),过滤字段加在子类。`GET` 接口才用 `Query`。
