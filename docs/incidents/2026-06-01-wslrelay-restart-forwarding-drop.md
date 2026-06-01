# 2026-06-01 WSL 服务重启后 wslrelay localhost 转发整体掉线

> 分类:工具栈运维 / WSL 网络。`search_docs(category="tooling_incident")` 可召回。

## 现象

平台迁到 WSL(systemd 常驻 19xxx)后,为让新建的 codev-platform codegraph 库立即生效,手动 `sudo systemctl restart codev-mcp-codegraph`。重启后:

- WSL **内部** 探 `127.0.0.1:19091/healthz` = 200,`ss -ltnp` 显示 python 正常监听 → 服务本身健康。
- Windows **外部** 探 `127.0.0.1:19091` = 无法连接,且 `Get-NetTCPConnection -LocalPort 19091` 无监听。
- 进一步全量探:`19083 / 19086 / 19091 / 19099 / 8848` 从 Windows **全部 DOWN**,`wslrelay` 进程整体消失。
- 只有 Gitea `3000` 仍通(它走静态 `netsh portproxy 127.0.0.1:3000 → <WSL IP>:3000`,不依赖 wslrelay)。

等 48s+ 不自愈。即:**单个 WSL systemd 服务重启,churn 掉了整个 WSL2 localhost 转发层**。

## 根因

- `.wslconfig` = `networkingMode=nat` + `localhostForwarding=true`。NAT 模式下 Windows→WSL 的 `127.0.0.1:port` 全靠 `wslrelay` 自动转发。
- 平台服务在 WSL 内绑的是 **`127.0.0.1`(loopback),不是 `0.0.0.0`** → 只能经 wslrelay 到达,**无法用 netsh portproxy 指向 WSL eth0 IP 兜底**(portproxy 连不上 loopback)。
- 触发点:重 reindex(GPU 模型 + 4500+ chunks 嵌入 + codegraph 建库)叠加紧接着的 `systemctl restart`,使监听进程切换;NAT 模式 wslrelay 偶发不重新绑定,且这次连带整个 relay 退出。这是 WSL2 NAT + localhostForwarding 的已知脆弱点,非平台代码缺陷。
- **诱因不是 Windows 本地 18xxx 平台服务**:18xxx 与 19xxx 端口不冲突,且停掉 18xxx 后转发仍正常(已验证),转发是在更晚的 restart 后才掉。

## 修复

`wsl --shutdown`(重置整个 WSL2 网络层)→ `wsl -d Ubuntu -- true` 重新拉起 → systemd 自动回 6 服务(均 `enabled`)→ Windows 转发全恢复。

- 验证:Windows 探 5 端口全 200,`19083`(chroma)约 48s 后 OK = Qwen 模型预热,符合预期。
- 代价:全平台重启 + chroma 预热 ~30-60s,期间 Windows MCP 短暂全红。

## 预防

1. **新建项目索引后不要手动重启 codegraph 端点**:codegraph 是多租户懒加载,下次首查该 `project_id` 会自动经 junction 读到新库,**无需 restart**。这次的 restart 本可避免,反而 churn 了转发。
2. **真要重启 WSL 服务时**,优先在低负载时做,避免叠加重 reindex;若重启后 Windows 连不上,先在 WSL 内部 `curl 127.0.0.1:<port>/healthz` 确认是"服务死"还是"转发死",转发死直接 `wsl --shutdown` 一步到位,不要逐服务重启(会反复 churn)。
3. **Windows 不再跑本地平台服务**:平台统一在 WSL,Windows 仅经 `.mcp.json`(SSE 127.0.0.1:19xxx)消费。18xxx 历史遗留进程已 kill,两仓 `.mcp.json` 均指向 19xxx,无自启会复活;唯一会拉起的是手动 `serve-mcp start`(Windows 上不要再跑)。
4. **保留 "Codev Refresh WSL Gitea PortProxy" 计划任务**:它维护 Gitea 的静态 portproxy(WSL IP 变动时刷新),是 wslrelay 死时 Gitea 仍可达的原因,**勿删**。
5. 候选改进:把平台服务在 WSL 内改绑 `0.0.0.0` 并配套静态 portproxy,可在 wslrelay 死时仍经 WSL IP 兜底访问(需评估安全:0.0.0.0 暴露面 + 反代/防火墙)。

## 关联

- 触发上下文:本会话先停 Windows 18xxx → openclaw-stock force 重建索引 → codev-platform 首次 codegraph init+index → 重启 codegraph 端点触发本故障。
- 规则:`ai-tools-mcp.md`(MCP 服务化 + 常驻依赖)、`windows-powershell.md`。
- 用户记忆:WSL 转发类故障另见 `wsl-mirrored-fixes-clash-tun`(Clash TUN 劫 WSL 网段,是"一开始就连不上"型,与本次"先好后坏"区分)。
