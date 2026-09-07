# 规则:库存扣减硬约束

> 虚构示例项目 AcmeShop 的工程规则,演示用。沉淀自 2026-05-20 超卖事故。

## 1. 铁律:扣减必须原子

任何对 `acme_inventory.available_qty` 的减法,**禁止「先 SELECT 后 UPDATE」两步**。必须二选一:

- **条件 UPDATE**(首选):`UPDATE ... SET available_qty = available_qty - :n WHERE sku = :sku AND available_qty >= :n`,以 `affected_rows` 判断成败。
- **Redis + Lua 原子扣减**(热点 SKU):DB 异步对账,但 Redis 侧必须 Lua 单脚本完成判断与扣减。

## 2. 禁止

- 禁止在应用层 `if (qty > 0) { update }` 判断库存——并发下必穿透。
- 禁止扣减后才补加唯一约束 / CHECK。`available_qty` 列必须有 `CHECK (available_qty >= 0)` 兜底,作为最后一道防线。
- 禁止跳过 `Idempotency-Key`:同一订单重试不得重复扣减。

## 3. 校验清单

提交涉及库存的改动前自检:

- [ ] 扣减 SQL 是单条条件 UPDATE,没有先查后写?
- [ ] 用 `affected_rows == 0` 判断无货并返回明确错误码?
- [ ] `acme_inventory.available_qty` 有 `CHECK (available_qty >= 0)`?
- [ ] 写接口带 `Idempotency-Key`,服务端去重?
- [ ] 热点 SKU 走 Redis 原子扣减,且 DB 对账任务幂等?

## 4. grep 自检

```bash
# 找「先查后扣」反模式:同一文件里既 SELECT available_qty 又 UPDATE
grep -rn "SELECT available_qty" acme-api/src/main/
grep -rn "available_qty = available_qty -" acme-api/src/main/
```

命中后人工核对二者是否在同一事务内、是否用了条件 UPDATE。
