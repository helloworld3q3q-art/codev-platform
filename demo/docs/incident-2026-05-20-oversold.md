# 事故复盘:2026-05-20 大促超卖

> 虚构示例项目 AcmeShop 的事故复盘,演示用。

## 一、现象

2026-05-20 20:00 大促开场 3 分钟内,爆款商品 `SKU-8842` 被下单 1200 件,但实际库存只有 1000 件,超卖 200 件,触发客服大量退款工单。

## 二、时间线

- 20:00:00 活动开始,瞬时 QPS 从 50 飙到 4000。
- 20:00:30 库存扣减接口 P99 从 20ms 涨到 1.2s。
- 20:02:10 监控发现 `acme_inventory.available_qty` 出现负值。
- 20:05:00 紧急对该 SKU 关单,停止下单。

## 三、根因

库存扣减用的是「先查后扣」两步:

```sql
SELECT available_qty FROM acme_inventory WHERE sku = ?;   -- 读到 5
UPDATE acme_inventory SET available_qty = available_qty - 1 WHERE sku = ?;
```

高并发下多个请求读到同一个 `available_qty`,各自判断「还有货」,导致扣减穿透。**读和扣之间没有原子性保证**。

## 四、修复

- 改为单条原子 UPDATE 带条件,不再先查:

```sql
UPDATE acme_inventory
   SET available_qty = available_qty - 1
 WHERE sku = ? AND available_qty >= 1;
```

`affected_rows = 0` 即判定无货,直接返回「已抢光」。

- 大促前对爆款 SKU 预热到 Redis,用 Lua 脚本原子扣减,DB 异步落账。

## 五、教训

- 任何「先查后写」的库存 / 余额扣减都要假设并发,要么用条件 UPDATE,要么用分布式锁,不能靠应用层判断。
- 大促压测必须覆盖单 SKU 热点,不能只看整体 QPS。
