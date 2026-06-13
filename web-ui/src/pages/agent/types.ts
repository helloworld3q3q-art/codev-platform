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
  // streaming: token 到达期(驱动 Bubble 原生 streaming prop —— 平滑增量、不重置 typing)。
  // animating: typing 动画播放中(done 后仍 true, 让动画按 interval 播完, onTypingComplete 才置 false)。
  // 两者分离: 模型很快吐完(streaming 窗口短)时, animating 仍撑住 typing 直到视觉打字结束。
  streaming?: boolean;
  animating?: boolean;
}
