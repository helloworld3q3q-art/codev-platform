# Agent 多模型接入架构(协议族,非厂商)

> 约束 `codev_platform/agent/brain/` 的 LLM provider 接入方式。详细说明见 `codev_platform/agent/brain/README.md`。

## 1. 铁律:按协议族分文件,不按厂商分

❌ **禁止"每个模型一个文件"** —— 那是把数据(厂商)当代码,加一家加一文件、大量重复、改一处改 N 处。

✅ 适配器按**协议族**组织:
- `anthropic.py` = Anthropic 协议(Claude,`tool_use` blocks)
- `openai_compat.py` = OpenAI 协议族(GPT / DeepSeek / 通义千问 / Kimi / 智谱 / Groq / Ollama / vLLM 等,**一个文件全吃**)

判定:厂商是 **config 数据**,协议族才是 **代码**。市面 95% 模型走 OpenAI 兼容协议。

## 2. 加模型走三档,只有第三档才加文件

| 场景 | 做法 | 改代码 |
|---|---|---|
| OpenAI 兼容新厂商 | config 加 `providers.<name> = {base_url, model, api_key}` | 零 |
| 想内置默认值的常用厂商 | `registry.py` 加一行 `register_provider(ProviderSpec(...))` | 一行 |
| 全新协议(非 OpenAI/Anthropic) | 新写 1 个适配器(实现 `LLMProvider`)+ 注册一行 | 一个文件 |

## 3. 强制机制(已落地,不可绕过)

- **策略接口**:所有适配器实现 `brain/base.py:LLMProvider`,loop 只依赖它。
- **中性类型**:loop 只认 `brain/types.py` 的中性类型;provider 适配器负责中性↔原生双向翻译。
- **配置驱动**:`registry.py` 按 `config.agent.provider` 选,遍历注册表,**零 if-else 分支**。
- **provider 专属字段走 extra**:思考模型 `reasoning_content` 等用 `Message.extra` / `AssistantTurn.extra` 不透明往返,**禁止**为单厂商在中性类型加专用字段。
- **key 解析**:`env > config.agent.providers.<name>.api_key`;真 key 不进 git。

## 4. loop 硬护栏(弱模型防空转)

弱模型指令遵从差,光靠 prompt 劝不动 → `loop.py` 加代码级护栏:
- **重复调用拦截**:同 `(tool, args)` 指纹重复 → 不执行,回灌提示逼换路/收尾。
- **倒数步强制收尾**:接近 `max_steps` → 工具结果追加"立即用现有证据收尾"提示。

新增 loop 控制逻辑时,优先用**代码硬约束**而非 prompt 文案(prompt 对弱模型不可靠)。

## 5. PR 自检

- [ ] 新增厂商:是否只改了 config / registry 一行,而**没有**新建厂商专属文件?
- [ ] 新建适配器文件:是否因为是**全新协议**(而非又一个 OpenAI 兼容厂商)?
- [ ] provider 专属字段:是否走 `extra` 而非污染中性类型?
- [ ] loop 行为约束:是否用代码护栏而非仅 prompt?

## 6. 已有 assertion 兜底

🟡 部分 assertion 化

已有:
- `tests/test_agent_registry.py`(注册表可扩展 + config-only 兜底 + 未知报错)
- `tests/test_agent_openai_translate.py` / `test_agent_anthropic_translate.py`(协议翻译正确性)
- `tests/test_agent_loop_guard.py`(重复调用拦截)

盲区(可补):
- 缺:静态扫描断言 `brain/` 下适配器文件数 ≤ 协议族数(防"每厂商一文件"复发)
