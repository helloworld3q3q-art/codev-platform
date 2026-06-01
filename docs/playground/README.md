# 只读问答 Playground

`index.html` 是一个**纯静态、无构建**的单文件页面,让非开发人员通过浏览器向平台 agent 提项目问题(**只读**,不写任何数据)。

## 用法

1. 起后端 agent 服务:

   ```bash
   codev-platform agent serve            # 默认 http://127.0.0.1:8848
   codev-platform agent serve --host 0.0.0.0 --port 8848   # 对外开放(注意鉴权)
   ```

2. 打开页面:直接双击 `index.html`,或用任意静态服务器托管(如 `python -m http.server`)后浏览器访问。

3. 填表提交:
   - **平台地址**:agent serve 的地址(默认 `http://127.0.0.1:8848`)。
   - **project_id**:要提问的项目(如 `codev-platform`),作为 body `project_id` + `X-Project-Id` 头发送;留空走后端 cwd 回退。
   - **平台 token**:token 鉴权模式必填(作 `Authorization: Bearer <token>`);dev 单机不填走 `X-User-Id`/`local` 回退。
   - **问题**:自然语言问题。

   提交后 `POST {base}/chat`,body `{question, project_id?}`。页面渲染 `answer` 及可折叠 `steps`(thought / tool / args / result_summary),并显示 session / stop_reason / usage。

## 前提与排错

- 后端需平台 venv 装好 `fastapi` + `uvicorn`,且配好 provider key(`config.agent.providers`)。缺 key 时 `/chat` 返 **503**。
- project 访问被拒返 **403**(检查 token / project_id)。
- 浏览器报 `Failed to fetch` / CORS:用同源静态服务器打开本页,或在 agent 端按需加 CORS 中间件。

## 接口契约(对应 `codev_platform/agent/routes/chat.py`)

- 请求:`POST /chat`,`{question, session_id?, max_steps?, project_id?}` + 头 `X-Project-Id` / `Authorization: Bearer`。
- 响应:`{session_id, answer, steps[{n,thought,tool,args,result_summary}], usage, stop_reason}`。
