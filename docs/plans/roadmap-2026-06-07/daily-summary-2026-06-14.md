# daily-summary 2026-06-14 —— /agent 对话端到端 SSE 流式 + 原生打字(挖出两个真 bug)

> 承 6-13 晚:用户反馈 web `/agent` 页两个症状 ——「没有打字效果」+「`/api/v1/agent/chat` 总是请求超时」,怀疑 `proxy.ts` 代理问题。
> 本线把它从「答非所问的猜代理」做到「逐字打字 + 不超时」,中途**两次猜错被实测纠正**,根因都不在 proxy:一个是 Starlette 线程模型导致的 contextvar 跨线程报错,一个是 Bubble 组件 `contentRender` 返回 JSX 关闭了原生打字。
> commit 链(本线):`9767d6d`→`9ed8822`→`f170af3`→`6b18b7a`→`6c9a5ba`→`3c4ec09`→`6e3f08b`(其间 `f5020ec`/`3216781` 是另一窗口 graph/Phase4,不属本线)。

## 一、起点:两个症状共一个误判方向(proxy)

用户怀疑 `web-ui/config/proxy.ts`。**实测否证**:proxy.ts 只配 `target + changeOrigin`,既不缓冲 SSE 也不是超时限制点。两个症状各有真因,且都不在 proxy:

- **超时真因**:前端 axios 全局 `timeout:30000` + 后端 `agent.timeout_sec` 默认 30s,两道 30s 串联;agent loop(deepseek、max_steps 12、~10万 token)轻松超 30s → 浏览器先 abort。
- **打字真因**:全栈本就**非流式**(agent `/chat` 返回一次性 `ChatResponse`),没有任何一层流式 → 没有 token 增量 → 自然没打字。

→ 用户拍板「要做就都做好」:端到端 SSE 流式,一次根治两者(流式连接不受 30s 阻塞超时影响 + 逐字出)。

## 二、端到端 SSE 流式(接通架构早预留的接缝)

`base.LLMProvider.stream` / `types.StreamEvent` / `routes/chat.py` 注释的 `/chat/stream` 本就是规划好的扩展点,本次完整接通。**非流式路径全保留作回退**。

- **brain**(`9767d6d`):`anthropic` / `openai_compat` 实现 `stream()`,两协议族都做(非为 deepseek 硬编)。Anthropic 用 `messages.stream + get_final_message`;OpenAI 用 `stream=True + stream_options.include_usage` + tool_calls 分片重组;`chat()`/`stream()` 共用组装函数。
- **loop**:`run_stream()` 设为**单一真值源**(yield token/step/done),`run()` 改为 drain 它取 done 的 AgentResult —— 护栏/planner 逻辑不复制一份;provider 无 `stream()` 时经 `NotImplementedError` 回退 `chat()`。
- **ChatService**:抽 `_build_loop`/`_persist` 给 `ask`/`ask_stream` 共用;`ask_stream` 转发 token/step、loop 收尾后落库、末尾补带 `session_id` 的 done。
- **agent route**:`POST /chat/stream` 返 `StreamingResponse`(SSE),`_authorize` 与 `/chat` 共享鉴权。
- **web 透传**(`9ed8822`):`POST /api/v1/agent/chat/stream` 哑透传(`include_in_schema=False`,不进 OpenAPI,不用跑 `pnpm run api`);`AgentClient.chat_stream` urllib readline 逐行透传上游 SSE。**SSE 传输原语下沉到共享 `utils/fetch.sseRequestStream`**(复用 axios 同款鉴权头 + 401 处理),页面只做 SSE 帧解析 —— 与姊妹项目 `ai-apps-web`「传输在 utils/fetch、解析在页面」分层一致(该版 `@ant-design/x` 无 `XStream`,解析需手写)。
- **前端**:`pages/agent/stream.ts` 原生 fetch + ReadableStream 读 SSE;token 增量、step 实时入 ToolFlow、done 对账,流式失败回退 `postAgentChat`。

## 三、两个真 bug(都不在 proxy,实测/读源码定位)

### Bug 1:contextvar 跨线程 `ValueError`(`f170af3`)
症状:web→agent 计时实测「token 逐步流出 ✓」但**末尾是 `error` 帧不是 `done`**。查 codev-agent 日志:`ValueError: <Token> was created in a different Context`,在 `ask_stream` 的 `reset_run_context`。
根因:`StreamingResponse` 把**同步生成器**丢进 Starlette threadpool,每次 `__next__` 可能换线程 → `set/reset_run_context`(RunContext contextvar)跨 Context 报错被兜成 internal error;且 loop 内工具也可能读不到 RunContext。
修:agent 路由把整段同步 `ask_stream` 固定在**单个**工作线程(`anyio.to_thread.run_sync`),SSE 帧经 `anyio` memory stream 桥回事件循环逐帧发 → contextvar 全程同线程一致。

### Bug 2:`contentRender` 返回 JSX 关闭 Bubble 原生 typing(`6c9a5ba`)
症状:流回来了、token 也到了,但前端**就是不打字**(只是比之前快)。读 `@ant-design/x` 的 `Bubble.js` 源码:
```js
const memoedContent = contentRender ? contentRender(content) : content;
const usingInnerAnimation = !!typing && typeof memoedContent === 'string';
```
assistant 气泡挂 `contentRender: renderMarkdown` 返回 `<XMarkdown>`(**JSX 非 string**)→ `usingInnerAnimation=false` → `TypingContent` 根本不启用 → `typing`/`streaming` 配置全是白调。参考项目同样 no-op,靠**慢模型增量 setState** 看着像打字;deepseek 太快(0.4s 吐完)就穿帮。
修:`contentRender` 从 role 移到 per-item,**打字期(`animating`)不挂** → content 保持 string 启用原生 typing,`onTypingComplete` 后再挂 markdown 渲染。用 `animating` 标志 + `onTypingComplete` 把打字时长与模型快慢解耦(done 后仍播,播完才关)。

## 四、方法论教训

1. **不轻信「症状=最近改的那个文件」**:用户三次指向 proxy,实测全证伪。分跳计时(web→agent 逐行到达时间)一眼区分「数据没流出」vs「流出了但前端没渲染」。
2. **第三方组件不灵就读它源码**:Bubble 的 `usingInnerAnimation = typing && typeof content==='string'` 一行定生死,读了才知道 contentRender 返回 JSX 是杀手 —— 对着「能工作的参考项目」抄配置反而误导(它那份 typing 也是 no-op)。
3. **误诊期加的层要回收**:试错时加过 Bubble `streaming` prop + 独立 `streaming` 标志(以为高频 setState 重置动画),真因修复后它已多余 → 清理(见 §五),typing 机制只留 `animating` 一个标志。
4. **Starlette 同步生成器 + contextvar = 跨线程陷阱**:任何「同步生成器经 StreamingResponse 流式 + 内部用 contextvar」都要固定单线程消费(`to_thread.run_sync` + memory stream 桥接),否则 set/reset 跨 Context 崩。

## 五、清理冗余(`6e3f08b`)

真因修复后回收误诊期的层:
- 删 `streaming` 标志 + Bubble `streaming` prop(`animating`+`onTypingComplete` 已管住生命周期;`streaming` prop 仅对「有间隙慢流」有边际意义,deepseek 下观察不到)。
- 删 `streamAgentChat` 未接线的 `signal` 参数(无调用方传)。
- `loading` 门改用 `animating`;`sseRequestStream` 的 `signal` 作共享 fetch 工具标准能力保留。

## 六、配置 + 运维

- WSL `~/.codev-platform/config.json` `agent.timeout_sec` 30→180(非流式回退 + 流式 socket 读余量)。
- 重启 `codev-agent`(承载 `/chat/stream`)+ `codev-web`(承载 `/api/v1/agent/chat/stream` 透传);**不动 `codev-mcp-*`**,本会话 MCP 连接不受影响。
- 前端走 umi HMR,刷新即生效;后端改动 push 到 `dev` + WSL pull + 重启服务。

## 七、对话区高度(`3c4ec09`)

顺手修:`h-[80vh]` 任意值语法在本项目 UnoCSS 配置下被丢弃(见 `styles.md`)→ 改内联 `style={{ height: 'calc(100vh - Npx)' }}`(项目 graph/unifiedgraph 全高页面既定写法),N 为唯一微调旋钮(扣 ProLayout 头 + PageContainer header)。

## 验证

- provider stream 单测 + loop `run_stream` 事件序列单测 + `run()` 回退兼容(`tests/test_agent_stream.py`),59 个 agent 测试全绿。
- WSL 真 deepseek 实跑:逐 token 流出 + 末尾 `done`(error 帧消失)+ 会话落库 + usage 计量正常。
- `chroma/server.py` 本线**未触碰**(它 6-13 被审计批 `313a972`/`8b1623a` 改过 bind/信物闸,与流式无关)。

## 结论
`/agent` 这条线闭环:**逐字打字 ✓ / 工具步实时 ToolFlow ✓ / 不超时 ✓ / markdown 最终渲染 ✓ / 高度铺满 ✓**。两个真 bug(contextvar 跨线程、contentRender 关闭原生 typing)都不在用户最初怀疑的 proxy —— 实测与读源码定位,胜过对着现象/参考项目猜。
