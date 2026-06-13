import PageContainer from '@/components/PageContainer';
import { useModel } from '@umijs/max';
import { useCallback, useEffect, useRef, useState } from 'react';

import { getMessages, getSessions, postAgentChat } from '@/services/apis/agentapi';

import ChatPanel from './components/ChatPanel';
import MenuSwapPanel from './components/MenuSwapPanel';
import SessionSider from './components/SessionSider';
import SessionToggle from './components/SessionToggle';
import { streamAgentChat } from './stream';
import type { ChatMessage } from './types';

const MAX_STEPS = 12;

// 轻量聚合页: 只持状态 + 编排回调(loadSessions / 新建 / 选择 / 发送), 业务逻辑下沉到生成接口 + 子组件。
const AgentPage: React.FC = () => {
  const { currentProjectId } = useModel('project');
  const [sessions, setSessions] = useState<API.SessionItem[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const [sessionPanelOpen, setSessionPanelOpen] = useState<boolean>(false);
  const seqRef = useRef<number>(0);

  const toggleSessionPanel = useCallback((): void => setSessionPanelOpen((v) => !v), []);
  // 最近一次选中的会话 id(同步可读)。异步取消息返回后据它判断是否仍是当前会话,
  // 防止"切到 B 但 A 的慢响应后到、把 B 内容覆盖成 A"的竞态(最后选中者胜)。
  const activeSessionRef = useRef<string>('');

  const nextId = useCallback((): string => {
    seqRef.current += 1;
    return `m${seqRef.current}`;
  }, []);

  const loadSessions = useCallback(async (): Promise<void> => {
    try {
      const res = await getSessions({ limit: 50, offset: 0 });
      setSessions(res.data ?? []);
    } catch {
      setSessions([]);
    }
  }, []);

  const handleNewSession = useCallback((): void => {
    activeSessionRef.current = '';
    setActiveSessionId('');
    setMessages([]);
    seqRef.current = 0;
  }, []);

  const handleSelectSession = useCallback(
    async (sessionId: string): Promise<void> => {
      if (sessionId === activeSessionRef.current) {
        return; // 已是当前会话, 不重复加载
      }
      activeSessionRef.current = sessionId;
      setActiveSessionId(sessionId);
      setMessages([]); // 立即清空, 不残留上一会话内容
      setLoading(true);
      try {
        const res = await getMessages({ sessionId });
        if (activeSessionRef.current !== sessionId) {
          return; // 期间又切走了, 丢弃过期结果(最后选中者胜)
        }
        const history: API.SessionMessageItem[] = res.data ?? [];
        seqRef.current = 0;
        setMessages(
          history.map((m) => ({
            id: nextId(),
            role: m.role === 'user' ? 'user' : 'assistant',
            content: m.content ?? '',
            steps: m.steps, // 历史会话回看工具流(后端已持久化)
          })),
        );
      } catch {
        if (activeSessionRef.current === sessionId) {
          setMessages([]);
        }
      } finally {
        if (activeSessionRef.current === sessionId) {
          setLoading(false);
        }
      }
    },
    [nextId],
  );

  const handleSend = useCallback(
    async (question: string): Promise<void> => {
      const sentFor = activeSessionRef.current; // 发送时所处会话('' = 新会话), 用于回包后比对
      const userMsg: ChatMessage = { id: nextId(), role: 'user', content: question };
      const placeholder: ChatMessage = {
        id: nextId(), role: 'assistant', content: '', animating: true,
      };
      setMessages((prev) => [...prev, userMsg, placeholder]);
      setLoading(true);

      const stillCurrent = (): boolean => {
        return activeSessionRef.current === sentFor; // 切走则丢弃, 不污染当前视图(最后选中者胜)
      };
      const patch = (fields: Partial<ChatMessage>): void => {
        if (!stillCurrent()) {
          return;
        }
        setMessages((prev) =>
          prev.map((msg) => (msg.id === placeholder.id ? { ...msg, ...fields } : msg)),
        );
      };
      const bindSession = (sessionId?: string): void => {
        if (sessionId && stillCurrent()) {
          activeSessionRef.current = sessionId; // 新会话落定 id, 同步 ref
          setActiveSessionId(sessionId);
        }
      };

      // 非流式回退:流未建立 / 中途 error / 未收到 done 时走它(复用成熟同步接口)。
      const runFallback = async (): Promise<void> => {
        try {
          const res = await postAgentChat({ question, sessionId: sentFor || undefined, maxSteps: MAX_STEPS });
          if (!stillCurrent()) {
            return;
          }
          const data = res.data;
          bindSession(data?.sessionId);
          patch({ content: data?.answer ?? '', steps: data?.steps, usage: data?.usage, stopReason: data?.stopReason });
          loadSessions();
        } catch {
          patch({ content: '请求失败, 请重试', animating: false });
        }
      };

      // 流式优先:token 增量打字, step 实时入 ToolFlow, done 对账(权威 answer/steps 覆盖增量)。
      let liveText = '';
      const liveSteps: API.ChatStep[] = [];
      let doneOk = false;
      try {
        const streamed = await streamAgentChat(
          { question, sessionId: sentFor || undefined, maxSteps: MAX_STEPS },
          {
            onToken: (delta) => {
              liveText += delta;
              patch({ content: liveText, steps: [...liveSteps] });
            },
            onStep: (step) => {
              liveSteps.push(step as API.ChatStep);
              liveText = ''; // 上一段是"思考"(已入 ToolFlow), 下一段重新累积(下个思考或最终答案)
              patch({ content: '', steps: [...liveSteps] });
            },
            onDone: (data) => {
              doneOk = true;
              bindSession(data.sessionId);
              patch({
                content: data.answer,
                steps: (data.steps as API.ChatStep[] | undefined) ?? [...liveSteps],
                usage: data.usage,
                stopReason: data.stopReason,
              });
              loadSessions();
            },
            onError: () => {
              // 中途 error 帧 → 不在此处理, 落到下面 !doneOk 分支统一回退非流式
            },
          },
        );
        if (!streamed || !doneOk) {
          await runFallback();
        }
      } finally {
        if (stillCurrent()) {
          setLoading(false);
        }
      }
    },
    [nextId, loadSessions],
  );

  // 某条 assistant 气泡 typing 动画播完 → 关其 animating(停打字光标 + 切回 markdown 渲染; 历史消息不受影响)。
  const handleTypingComplete = useCallback((id: string): void => {
    setMessages((prev) =>
      prev.map((msg) => (msg.id === id ? { ...msg, animating: false } : msg)),
    );
  }, []);

  // 切项目: 清空会话重开 + 重拉会话列表 (会话与 project 上下文绑定)。
  useEffect(() => {
    activeSessionRef.current = '';
    setActiveSessionId('');
    setMessages([]);
    seqRef.current = 0;
    loadSessions();
  }, [currentProjectId, loadSessions]);

  return (
    <PageContainer>
      {/* 高度铺满右侧内容区: 内联 calc(UnoCSS 任意值 h-[..] 在本项目配置下会被丢弃, 故用 style)。
          偏移 = ProLayout 头 + PageContainer header/面包屑 + 内容上下留白; 如有微小空隙/滚动条微调此值。 */}
      <div
        className="relative bg-#ffffff rounded-8 overflow-hidden border border-#f0f0f0 shadow-sm"
        style={{ height: 'calc(100vh - 60px)' }}
      >
        <ChatPanel messages={messages} loading={loading} onSend={handleSend} onComplete={handleTypingComplete} />
        {/* 贴左边缘(菜单右侧)的竖向小钮: 上下拖动 + 点击在"菜单 ↔ 会话列表"间切换 */}
        <SessionToggle open={sessionPanelOpen} onToggle={toggleSessionPanel} />
      </div>
      {/* 会话列表: 占据左侧菜单原位(菜单向左滑出隐藏), 再点小钮切回菜单 */}
      <MenuSwapPanel open={sessionPanelOpen}>
        <SessionSider
          items={sessions}
          activeKey={activeSessionId}
          onSelect={handleSelectSession}
          onNew={handleNewSession}
        />
      </MenuSwapPanel>
    </PageContainer>
  );
};

export default AgentPage;
