-- codev-platform memory: 一键初始化平台独立 PG 库 (codev_platform_memory)
--
-- 红线 (memory plan §3.2b): memory 数据严禁与任何业务库同 database。
-- 本脚本只在现有 PG server 内建「独立 role + 独立 database + 授权」,绝不碰业务库。
-- 表结构 (agent_sessions / agent_messages 等) 由应用首次连接时幂等自动建 (CREATE IF NOT EXISTS),
-- 故本脚本不建表 —— 只准备一个空库交给 app。
--
-- ============================================================================
-- 执行者: 需 postgres 超级用户 (codev-platform 代码无超管密码, 不自动建库)。
-- 执行前必改: 把下面的占位密码 '<CHANGE_ME>' 改成你自己的强密码,
--             这个密码要原样填进 config.memory.pg_dsn (见 scripts/README.md 初始化一节)。
--
-- 注意 CREATE DATABASE 不能在事务块/DO 块里跑, 故分两步 (见文件末尾说明)。
-- ============================================================================

-- ---- 步骤 1: 建独立 role (幂等; PG 无 CREATE ROLE IF NOT EXISTS, 用 DO 块判存在) ----
DO
$$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'codev_platform') THEN
    -- 改这里的密码! 原样同步到 config.memory.pg_dsn。
    CREATE ROLE codev_platform LOGIN PASSWORD '<CHANGE_ME>';
  END IF;
END
$$;

-- ---- 步骤 2: 建独立 database, OWNER = codev_platform ----
-- 不能放进上面的 DO 块 (CREATE DATABASE 禁止在事务内执行)。
-- 若库已存在会报错, 属正常 (说明已初始化过), 忽略即可。
CREATE DATABASE codev_platform_memory OWNER codev_platform;

-- ---- 步骤 3: 授权 ----
GRANT ALL PRIVILEGES ON DATABASE codev_platform_memory TO codev_platform;
-- schema public 的授权必须在「新库内」执行,故用 \connect 切过去 (psql 元命令,-f 单文件可用)。
-- OWNER 通常已对自己库的 public 有权;PG15+ public 默认收紧,这行确保 owner 能建表。
\connect codev_platform_memory
GRANT ALL ON SCHEMA public TO codev_platform;

-- ============================================================================
-- 一行跑法 (psql, postgres 超管身份):
--   psql -U postgres -f scripts/init-memory-db.sql
-- 若步骤 2 报 "database already exists" 而前面成功, 说明已就绪, 无需理会。
--
-- 完成后验证 (用新 role 连新库, 会提示输入上面设的密码):
--   psql -h localhost -U codev_platform -d codev_platform_memory -c "SELECT current_database(), current_user;"
-- ============================================================================
