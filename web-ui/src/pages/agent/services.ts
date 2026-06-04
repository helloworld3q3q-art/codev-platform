// agent services 适配层: 把自动生成的 API 包成 fetchXxx, 剥掉 CommonResult 外壳直接拿 data。
// 照 codegraph/common/services.ts 模式; 后端契约一律走 API.* (typings.d.ts), 本层只做剥壳。

import {
  getMessages,
  getSessions,
  postAgentChat,
} from '@/services/apis/agentapi';

export async function fetchAgentChat(
  req: Partial<API.ChatRequest>,
): Promise<API.ChatData | undefined> {
  const res = await postAgentChat(req);
  return res.data as API.ChatData | undefined;
}

export async function fetchSessions(): Promise<API.SessionItem[]> {
  const res = await getSessions({ limit: 50, offset: 0 });
  return (res.data as API.SessionItem[] | undefined) ?? [];
}

export async function fetchSessionMessages(
  sessionId: string,
): Promise<API.SessionMessageItem[]> {
  const res = await getMessages({ sessionId });
  return (res.data as API.SessionMessageItem[] | undefined) ?? [];
}
