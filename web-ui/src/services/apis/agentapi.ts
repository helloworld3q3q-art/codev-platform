import {
  post,
} from '@/utils/fetch';

const commonUrl = '';

// 对话-代理到 agent 后端
export async function postAgentChat(data: Partial<API.ChatRequest>): Promise<API.CommonResult_ChatData_> {
  return await post<API.CommonResult_ChatData_>({
    url: `${commonUrl}/api/v1/agent/chat`,
    data,
  });
}

