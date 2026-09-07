import {
  get,
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

// 会话-列表(按当前用户)
export async function getSessions(data: Partial<API.AgentGetSessionsParams>): Promise<API.CommonResult_list_SessionItem__> {
  return await get<API.CommonResult_list_SessionItem__>({
    url: `${commonUrl}/api/v1/agent/sessions`,
    data,
  });
}

// 会话-历史消息
export async function getMessages(data: Partial<API.SessionsGetMessagesParams>): Promise<API.CommonResult_list_SessionMessageItem__> {
  return await get<API.CommonResult_list_SessionMessageItem__>({
    url: `${commonUrl}/api/v1/agent/sessions/messages`,
    data,
  });
}

