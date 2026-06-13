// 流式对话 SSE 解析器 —— 传输(fetch + 鉴权头 + 401)走共享 @/utils/fetch 的 sseRequestStream,
// 本文件只负责把后端 SSE 帧(data: {"kind","data"}\n\n)解析成业务回调。与姊妹项目把传输放
// utils/fetch、解析放页面侧的分层一致(本版 @ant-design/x 无 XStream, 解析需手写)。

import { sseRequestStream } from '@/utils/fetch';

export interface StreamStep {
  n?: number;
  thought?: string | null;
  tool?: string | null;
  args?: unknown;
  resultSummary?: string | null;
}

export interface StreamDone {
  sessionId?: string;
  answer: string;
  steps?: StreamStep[];
  usage?: Record<string, unknown>;
  stopReason?: string;
}

export interface StreamHandlers {
  onToken: (delta: string) => void;
  onStep: (step: StreamStep) => void;
  onDone: (data: StreamDone) => void;
  onError: (message: string) => void;
}

const asObj = (v: unknown): Record<string, unknown> => {
  if (v && typeof v === 'object') {
    return v as Record<string, unknown>;
  }
  return {};
};

const mapStep = (d: unknown): StreamStep => {
  const o = asObj(d);
  return {
    n: o.n as number | undefined,
    thought: o.thought as string | null | undefined,
    tool: o.tool as string | null | undefined,
    args: o.args,
    resultSummary: (o.result_summary ?? o.resultSummary) as string | null | undefined,
  };
};

const dispatch = (evt: unknown, h: StreamHandlers): void => {
  const o = asObj(evt);
  const kind = o.kind;
  if (kind === 'token') {
    h.onToken(String(o.data ?? ''));
    return;
  }
  if (kind === 'step') {
    h.onStep(mapStep(o.data));
    return;
  }
  if (kind === 'done') {
    const d = asObj(o.data);
    h.onDone({
      sessionId: (d.session_id ?? d.sessionId) as string | undefined,
      answer: String(d.answer ?? ''),
      steps: Array.isArray(d.steps) ? (d.steps as unknown[]).map(mapStep) : undefined,
      usage: d.usage as Record<string, unknown> | undefined,
      stopReason: (d.stop_reason ?? d.stopReason) as string | undefined,
    });
    return;
  }
  if (kind === 'error') {
    h.onError(String(asObj(o.data).message ?? 'stream error'));
  }
};

// 返回是否走通了流式(true=正常读完;false=未建流, 调用方回退非流式)。
export async function streamAgentChat(
  body: { question: string; sessionId?: string; maxSteps?: number },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<boolean> {
  let stream: ReadableStream<Uint8Array>;
  try {
    stream = await sseRequestStream({ url: '/api/v1/agent/chat/stream', data: body, signal });
  } catch {
    return false; // 建连失败(含 401 已跳登录)→ 调用方回退非流式
  }
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buf += decoder.decode(value, { stream: true });
    let idx = buf.indexOf('\n\n');
    while (idx >= 0) {
      const frame = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 2);
      if (frame.startsWith('data:')) {
        const payload = frame.slice(5).trim();
        try {
          dispatch(JSON.parse(payload), handlers);
        } catch {
          // 跳过坏帧, 不中断流
        }
      }
      idx = buf.indexOf('\n\n');
    }
  }
  return true;
}
