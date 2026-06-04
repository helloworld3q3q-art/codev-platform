import PageContainer from '@/components/PageContainer';
import { useModel } from '@umijs/max';
import { useCallback, useEffect, useRef, useState } from 'react';

import { getMessages, getSessions, postAgentChat } from '@/services/apis/agentapi';

import ChatPanel from './components/ChatPanel';
import SessionSider from './components/SessionSider';
import type { ChatMessage } from './types';

const MAX_STEPS = 12;

// 轻量聚合页: 只持状态 + 编排回调(loadSessions / 新建 / 选择 / 发送), 业务逻辑下沉到 services + 子组件。
const AgentPage: React.FC = () => {
  const { currentProjectId } = useModel('project');
  const [sessions, setSessions] = useState<API.SessionItem[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const seqRef = useRef<number>(0);

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
    setActiveSessionId('');
    setMessages([]);
    seqRef.current = 0;
  }, []);

  const handleSelectSession = useCallback(
    async (sessionId: string): Promise<void> => {
      setActiveSessionId(sessionId);
      setLoading(true);
      try {
        const res = await getMessages({ sessionId });
        const history: API.SessionMessageItem[] = res.data ?? [];
        seqRef.current = 0;
        setMessages(
          history.map((m) => ({
            id: nextId(),
            role: m.role === 'user' ? 'user' : 'assistant',
            content: m.content ?? '',
          })),
        );
      } catch {
        setMessages([]);
      } finally {
        setLoading(false);
      }
    },
    [nextId],
  );

  const handleSend = useCallback(
    async (question: string): Promise<void> => {
      const userMsg: ChatMessage = { id: nextId(), role: 'user', content: question };
      const placeholder: ChatMessage = { id: nextId(), role: 'assistant', content: '' };
      setMessages((prev) => [...prev, userMsg, placeholder]);
      setLoading(true);
      try {
        const res = await postAgentChat({
          question,
          sessionId: activeSessionId || undefined,
          maxSteps: MAX_STEPS,
        });
        const data = res.data;
        if (data?.sessionId) {
          setActiveSessionId(data.sessionId);
        }
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === placeholder.id
              ? {
                ...msg,
                content: data?.answer ?? '',
                steps: data?.steps,
                usage: data?.usage,
                stopReason: data?.stopReason,
              }
              : msg,
          ),
        );
        loadSessions(); // 新会话首答后刷新侧栏, 让其出现在历史列表
      } catch {
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === placeholder.id ? { ...msg, content: '请求失败, 请重试' } : msg,
          ),
        );
      } finally {
        setLoading(false);
      }
    },
    [nextId, activeSessionId, loadSessions],
  );

  // 切项目: 清空会话重开 + 重拉会话列表 (会话与 project 上下文绑定)。
  useEffect(() => {
    setActiveSessionId('');
    setMessages([]);
    seqRef.current = 0;
    loadSessions();
  }, [currentProjectId, loadSessions]);

  return (
    <PageContainer>
      <div className="flex h-700 bg-#ffffff rounded-6 overflow-hidden">
        <SessionSider
          items={sessions}
          activeKey={activeSessionId}
          onSelect={handleSelectSession}
          onNew={handleNewSession}
        />
        <div className="flex-1 min-w-0">
          <ChatPanel messages={messages} loading={loading} onSend={handleSend} />
        </div>
      </div>
    </PageContainer>
  );
};

export default AgentPage;
