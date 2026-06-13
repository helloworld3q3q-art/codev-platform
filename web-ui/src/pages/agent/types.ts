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
  // animating: typing 动画播放中(token 到达期 + done 后直到 onTypingComplete 播完)。驱动 Bubble
  // 原生 typing + 打字期不挂 contentRender(content 须为 string 才启用原生打字, 见 ChatPanel)。
  animating?: boolean;
}
