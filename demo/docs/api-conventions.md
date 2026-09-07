# AcmeShop API 约定

> 虚构示例项目 AcmeShop 的接口规范,演示用。

## 1. 统一响应体

所有 `acme-api` 接口返回统一信封:

```json
{ "code": 0, "message": "ok", "data": { } }
```

- `code=0` 表示成功;非 0 为业务错误码,前端按 `code` 分支,不靠 HTTP 状态。
- 列表接口 `data` 内含 `items` 与 `total`,分页参数为 `page`(从 1 起)与 `pageSize`(默认 20,上限 100)。

## 2. 命名规范

- 路径用小写连字符:`/api/v1/orders/{orderId}/items`。
- 字段一律 camelCase:`createdAt` / `availableQty` / `merchantId`。
- 时间戳统一 ISO 8601 带时区:`2026-06-01T09:30:00+08:00`。

## 3. 枚举字段

枚举值的真值源在 Java 端 `OrderStatusEnum`,前端不得硬编码。当前订单状态:

| 值 | 含义 |
|---|---|
| `CREATED` | 已创建待支付 |
| `PAID` | 已支付 |
| `SHIPPED` | 已发货 |
| `COMPLETED` | 已完成 |
| `CANCELLED` | 已取消 |

新增枚举值必须同步:Java enum → DTO `@Schema` → 前端生成的 typings,缺一不可。

## 4. 幂等与重试

- 写接口必须带 `Idempotency-Key` 请求头,服务端按 key 去重 24 小时。
- 客户端重试用指数退避,最多 3 次;5xx 才重试,4xx 直接报错给用户。
