# brain/ — LLM provider 抽象层

> agent 的"大脑"接入层。核心设计:**按协议族分文件,不按厂商分**。多模型可插拔的根。

## 目录

| 文件 | 职责 |
|---|---|
| `types.py` | 中性类型(`Message` / `ToolCall` / `ToolResult` / `AssistantTurn`)。agent loop 只认这套,不知道底下是哪家模型。`extra` 是 provider 往返用的不透明袋子(如思考模型的 `reasoning_content`)。 |
| `base.py` | `LLMProvider` 抽象(策略接口)。 |
| `anthropic.py` | **Anthropic 协议族**适配器(Claude:`tool_use` blocks)。 |
| `openai_compat.py` | **OpenAI 协议族**适配器(GPT / DeepSeek / 通义千问 / Kimi / 智谱 / Groq / Ollama / vLLM … 全吃)。 |
| `registry.py` | 声明式 provider 注册表(`ProviderSpec`)+ 配置驱动选择。 |

## 核心原则:协议族 ≠ 厂商

| | 厂商(vendor) | 协议族(protocol) |
|---|---|---|
| 数量 | 几十上百家,不断新增 | 就 2-3 种(OpenAI 族 / Anthropic 族 / 极少自定义) |
| 变化 | 频繁 | 稳定 |
| 归属 | **config 里的数据** | **代码(适配器)** |

❌ **每个模型一个文件 = 把数据当代码** —— 加一家加一文件、大量重复、改一处改 N 处。
✅ 厂商是 config 数据,协议族才是适配器。市面 95% 模型是 OpenAI 兼容 → 一个 `openai_compat.py` 全吃。

## 三档扩展姿势(从轻到重)

```
1. OpenAI 兼容的新厂商(最常见)  → 改 config 加一段,零代码
     config.agent.providers.kimi = { base_url, model, api_key }
     (registry 对未注册但配了 base_url 的 provider 自动按 OpenAI 兼容构建)

2. 想要内置默认值的常用厂商      → registry.py 加一行声明
     register_provider(ProviderSpec("kimi", "KIMI_API_KEY", _build_openai_compat,
                                     default_model="...", default_base_url="..."))

3. 全新协议(罕见,如某家自定义 REST/gRPC)→ 新写 1 个适配器文件 + 注册一行
     新 brain/acme.py(实现 LLMProvider)+ register_provider(...)
```

**只有第 3 档才加文件,且按协议加,不按厂商加。**

## 配置(单一来源)

```jsonc
// ~/.codev-platform/config.json
"agent": {
  "provider": "deepseek",        // 切换当前大脑,改这行
  "model": "deepseek-chat",      // 切换型号
  "max_steps": 12,
  "providers": {                 // 所有厂商声明(数据)
    "claude":   { "model": "claude-opus-4-7", "api_key": "" },
    "deepseek": { "base_url": "https://example.invalid/reference", "model": "deepseek-chat", "api_key": "..." }
  }
}
```

key 解析:`env(如 DEEPSEEK_API_KEY) > config.agent.providers.<name>.api_key`。真 key 只在本机 config(不进 git)或 env。

## 加新中性能力(如思考模型字段)

不要给某厂商专属字段开个新中性字段;用 `Message.extra` / `AssistantTurn.extra` 这个不透明袋子往返。例:`reasoning_content`(思考模型)由 `openai_compat` 捕获进 `extra`,下一轮原样回传,loop 全程不感知。

## 相关

- 规则:`rules/agent-provider-architecture.md`(本设计的强制约束)
- plan:`docs/plans/roadmap-2026-05-28/agent-2026-05-28.md` §1.2
