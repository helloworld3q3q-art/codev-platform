# AcmeShop 架构概览

> 本文档是 **虚构示例项目 AcmeShop** 的架构说明,仅用于演示 codev-platform 的文档检索能力。所有服务、表名、字段均为编造。

## 1. 系统定位

AcmeShop 是一个面向中小商家的电商后台,拆成三层:

- **前端**:`acme-web`(React + TypeScript),负责商品管理、订单看板、营销活动配置。
- **后端**:`acme-api`(Java + Spring Boot),提供 REST 接口、订单状态机、库存扣减。
- **数据管道**:`acme-pipeline`(Python),负责销量聚合、商品冷热分层、推荐召回。

三层共享一套 PostgreSQL 实例,核心表为 `acme_order` / `acme_product` / `acme_inventory`。

## 2. 关键链路

下单链路是全栈贯通的典型例子:

```
acme-web 提交订单
  → acme-api OrderController.create()
  → 库存服务扣减 acme_inventory.available_qty
  → 写 acme_order(status=CREATED)
  → 异步发 MQ 消息 order.created
  → acme-pipeline 消费, 更新销量聚合表 acme_sales_daily
```

## 3. 部署形态

- 单可用区双实例 + Nginx 反向代理。
- 数据库主从:主写从读,从库延迟告警阈值 5 秒。
- 灰度发布按商家 ID 哈希分桶,先放 5% 流量。

## 4. 为什么这样设计

- 库存扣减放在 API 层同步完成,避免超卖;销量聚合走异步管道,避免阻塞下单。
- 前端不直接读库,所有数据走 acme-api,保证权限与审计统一收口。
