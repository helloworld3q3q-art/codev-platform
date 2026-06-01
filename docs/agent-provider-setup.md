# Agent Provider(LLM 大脑)配置 —— api_key 填哪里

> 仅当你要用 **agent 问答 `/chat` / Web playground** 时需要。三套 MCP 知识平面(chroma 检索 / codegraph / cross-link)**不需要** key,照常工作。

## 一、key 填在哪里(二选一)

配置文件:**WSL 的 `~/.codev-platform/config.json`**(= `/home/helloworld/.codev-platform/config.json`,**用户级、不进 git**)。结构已由我预置好(4 家 provider,api_key 全空),你只需补**你用的那一家**。

### 方式 A —— 环境变量(推荐,key 不落 config)
`config.json` 里 api_key 留空即可,key 走 env。先把 `agent.provider` 改成你用的那家,再 export 对应变量:

| provider | agent.provider 值 | 环境变量 | 协议 |
|---|---|---|---|
| Claude(Anthropic) | `claude` | `ANTHROPIC_API_KEY` | Anthropic |
| OpenAI GPT | `gpt` | `OPENAI_API_KEY` | OpenAI 兼容 |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | OpenAI 兼容 |
| 通义千问 | `qwen` | `DASHSCOPE_API_KEY` | OpenAI 兼容 |

```bash
export DEEPSEEK_API_KEY=sk-你的key      # 例:用 deepseek
# 并确保 config.json 里 "agent": { "provider": "deepseek", ... }
```

### 方式 B —— 直接写 config.json(key 落本机文件,已 gitignore)
编辑 `~/.codev-platform/config.json`,找到 `agent.providers.<你用的那家>.api_key`,把空串换成 key:
```jsonc
"agent": {
  "provider": "deepseek",                 // ← 改成你用的那家
  "providers": {
    "deepseek": { "base_url": "https://api.deepseek.com", "model": "deepseek-chat",
                  "api_key": "sk-你的key" }   // ← 只改这一行
  }
}
```
> 优先级:**env > config**。两个都设时 env 赢。真 key 绝不进 git(config.json 已 gitignore)。

## 二、让 agent 真能用(playground 同理)
agent HTTP 服务需要 **fastapi**(平台 venv 默认没装):
```bash
~/work/codev-platform/.venv/bin/pip install fastapi uvicorn        # 装 agent 运行依赖
sudo systemctl enable --now codev-agent                            # 起常驻服务(unit 已装好, 之前因缺 fastapi 被停)
systemctl is-active codev-agent                                    # 应 active
```
起来后:`docs/playground/index.html` 填平台地址 + project_id + 问题即可问答。

## 三、当前状态(2026-06-01 我已配好的)
- `~/.codev-platform/config.json` 的 `agent` 段已预置:`provider=claude` + 4 家 providers(**api_key 全空,待你填**)。
- `codev-agent.service` 已装好但**已 disable**(缺 fastapi,起不来);装 fastapi 后按上面 enable。
- 其余 6 服务(chroma/cross-link/codegraph/reindex/webhook/clock-resync)**全 active**,跑最新代码。

## 四、Windows 那个明文 key(审计 #7)
本次体检发现 **WSL config 无 provider key**(干净)。审计 #7 那个明文 key 多半在 **Windows** 的 `C:\Users\G1706256\.codev-platform\config.json`。查一下:
```powershell
type C:\Users\G1706256\.codev-platform\config.json
```
若 `agent.providers.*.api_key` 有明文 → 去 provider 控制台**吊销旧 key、换新**,然后按本文方式 A(env)或 B(config)重填。
