// agent services 适配层: 把自动生成的 postAgentChat 包成 fetchAgentChat, 剥掉 CommonResult 外壳直接拿 data。
// 照 codegraph/common/services.ts 模式。

import { postAgentChat } from '@/services/apis/agentapi';

export async function fetchAgentChat(
  req: Partial<API.ChatRequest>,
): Promise<API.ChatData | undefined> {
  const res = await postAgentChat(req);
  return res.data as API.ChatData | undefined;
}
