// 流式对话 SSE 读取器 —— 生成接口层(axios)无法读 ReadableStream, 故对 /chat/stream 用原生
// fetch + ReadableStream。鉴权头手动复刻 fetch.ts 拦截器(Authorization / X-Project-Id /
// X-Org-Id), 因绕过了 axios 实例。后端帧形: data: {"kind","data"}\n\n(kind=token|step|done|error)。

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

const buildHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem('auth_token');
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const projectId = localStorage.getItem('current_project');
  if (projectId) {
    headers['X-Project-Id'] = projectId;
  }
  const orgId = localStorage.getItem('current_org');
  if (orgId) {
    headers['X-Org-Id'] = orgId;
  }
  return headers;
};

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

// 返回是否走通了流式(true=已 onDone;false=未建流, 调用方回退非流式)。
export async function streamAgentChat(
  body: { question: string; sessionId?: string; maxSteps?: number },
  handlers: StreamHandlers,
): Promise<boolean> {
  let resp: Response;
  try {
    resp = await fetch('/api/v1/agent/chat/stream', {
      method: 'POST',
      headers: buildHeaders(),
      body: JSON.stringify(body),
    });
  } catch {
    return false; // 建连失败 → 回退非流式
  }
  if (!resp.ok || !resp.body) {
    return false;
  }
  const reader = resp.body.getReader();
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
