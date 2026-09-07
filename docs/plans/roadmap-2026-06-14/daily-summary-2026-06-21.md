# daily-summary 2026-06-21 —— platform-docs MCP CUDA daemon 故障修复(cudaErrorUnknown 假绿)

> 起因:审计任务中 `search_docs` 全失效,被迫退回 grep+Read 兜底。用户点明"主要是这个"——根因不在审计,而在 platform-docs MCP 的 GPU daemon 崩了。
> 贯穿纪律:**症状(grep 兜底)≠ 根因(daemon 死)、实调验证而非看进程绿灯、修复必两步闭环**。

## 一、根因:cudaErrorUnknown 上下文损坏(区别于已知的 OOM)

- daemon(PID 166)在 10:31:57 撞 `torch.AcceleratorError: CUDA error: unknown error`(`cudaErrorUnknown`)—— WSL 宿主机 GPU 驱动重置 / 睡眠唤醒所致的 CUDA **上下文损坏**,不是干净的显存 OOM。
- **进程没退出** → systemd 报 `active (running)`、业务仓 `/mcp` 显示"已连接",但 CUDA 上下文已死、还占着 ~5.2GB 不可用显存(`nvidia-smi --query-compute-apps` 见 PID `used_memory:[N/A]`)。每次 `search_docs` 静默失败。
- **自动重生不触发**(进程没崩)→ 故障可无限潜伏,双绿欺骗性极强。

## 二、修复:服务端 + 客户端两步缺一不可

| 步 | 动作 | 验证 |
|---|---|---|
| ① 服务端 | WSL `sudo systemctl restart codev-mcp-platform-docs` | 显存 5195→**10**MiB(死上下文释放)→ 重载模型 2469MiB + prewarm ~50s;`/health` ok;两库(codev 2015 / openclaw 4013)重载 |
| ② 客户端 | **重启 Claude Code**(非 `/clear`) | 否则 MCP 客户端还连旧死会话 → tools/call 报 `-32602` + daemon 日志 `Received request before initialization was complete` |

**收口验证**(实调,非看进程):`list_collections`(无参)+ `search_docs`(走 GPU embedding+rerank,返回带 `distance`+`rerank_score` 真实语义结果)双双通过。

## 三、教训沉淀

1. **进程绿灯 ≠ 服务可用**:`cudaErrorUnknown` 让进程存活而 GPU 上下文死,systemd / `/mcp` 双绿但搜索全废。判活必**实调一个真 tool**(走 GPU 推理路径),不能只看 `is-active` / `/health`。
2. **症状别当根因**:grep 兜底只是 MCP 死的表象。用户一句"主要是这个"把注意力从审计拉回 daemon —— 排障先定位最上游故障源。
3. **修复闭环跨两侧**:daemon 重启(服务端)+ 客户端重握手缺一不可;只重启 daemon 客户端仍报 `-32602`。
4. **记忆已沉**:`agent-tool-health-and-gpu-oom` 补坑4(cudaErrorUnknown 假绿),与坑2(OOM 漏显存)并列;MEMORY.md 索引同步。

## 四、顺带推进的审计结论(MCP 恢复前已从代码侧坐实)

- **B1(`suggestedAmount`)**:`stock-web/src/pages` 零匹配 → C 端不渲染绝对金额,只展示仓位比例,非上线阻断项。
- **C 端回测/胜率/净值/绩效**:`src/pages` 对全套关键词零匹配,命中的 13 文件全在 `services/apis/*`(生成 DTO typings 残留)+ DisclaimerBanner/TabContainer/utils → C 端不展示任何历史绩效 → **H1(诊断绩效冒充策略净值)/ H2(回测开盘买)非 C 端 live bug**,降级为 admin/数据层残留风险,与 B1 同级。

## 旋钮 + 后续

- 候选加固:daemon 内加 CUDA 健康自检(周期性 dummy embed,撞 cudaErrorUnknown 即主动 `os._exit` 让 systemd 重生),把"进程假活"从人工排障变自愈。当前未做(未接线不写未来代码),记为 backlog。
- 审计待办:用恢复的 `search_docs` 对齐 openclaw-stock 库里 H1/H2/免责的合规口径(注意当前会话路由 codev-platform,查业务规则需确认 project_id)。
