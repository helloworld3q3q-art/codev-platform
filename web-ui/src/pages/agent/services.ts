// agent services 适配层: 把自动生成的 API 包成 fetchXxx, 剥掉 CommonResult 外壳直接拿 data。
// 照 codegraph/common/services.ts 模式; 接口绑定一律用 pnpm run api 生成的函数 + 类型,
// 不手写 / 不 as 强转 —— 生成的 CommonResult_*.data 已是契约类型, 直接 return res.data。

import {
  getMessages,
  getSessions,
  postAgentChat,
} from '@/services/apis/agentapi';

export async function fetchAgentChat(
  req: Partial<API.ChatRequest>,
): Promise<API.ChatData | undefined> {
  const res = await postAgentChat(req);
  return res.data;
}

export async function fetchSessions(): Promise<API.SessionItem[]> {
  const res = await getSessions({ limit: 50, offset: 0 });
  return res.data ?? [];
}

export async function fetchSessionMessages(
  sessionId: string,
): Promise<API.SessionMessageItem[]> {
  const res = await getMessages({ sessionId });
  return res.data ?? [];
}
