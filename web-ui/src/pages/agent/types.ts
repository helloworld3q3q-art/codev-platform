// agent 对话页类型 —— 仅本页 UI 状态用; 后端契约一律走 API.* (typings.d.ts)。

export type ChatRole = 'user' | 'assistant';

// 一条对话消息 (UI 视图)。assistant 携带 steps / usage 供折叠展示。
export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  steps?: API.ChatStep[];
  usage?: Record<string, unknown>;
  stopReason?: string;
  // 流式进行中标志: 驱动 Bubble 原生 typing 打字效果(true=打字, false/缺省=静态显示)。
  streaming?: boolean;
}
