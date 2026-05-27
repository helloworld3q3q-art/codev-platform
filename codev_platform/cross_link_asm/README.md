# codev_platform.cross_link_asm

Java ASM 字节码扫描器 — 解析 invoke 指令建调用图,扩展 cross-link KG 到 JVM 层。

**当前状态**:从 platform 仓 `tools/cross_link_asm/` 整目录迁来。`INTERNAL_PREFIX = "com/openclaw/"` 仍硬编码,新项目接入前需改 CLI 参数。

## 用法

```powershell
# 构建 (需 JDK 17+)
mvn -f codev_platform/cross_link_asm/pom.xml package

# 跑
java -jar target/cross-link-asm-*.jar <jar-or-classes-dir>
```

## TODO

- INTERNAL_PREFIX 改 CLI 参数(或读 ~/.codev-platform/config.json `cross_link.internal_prefix`)
- 输出格式与 codev_platform.cross_link.schema 对齐(直接写 nodes/edges 进 SQLite)
