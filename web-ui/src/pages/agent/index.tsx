import PageContainer from '@/components/PageContainer';
import { useModel } from '@umijs/max';
import { useCallback, useEffect, useRef, useState } from 'react';

import Composer from './components/Composer';
import MessageList from './components/MessageList';
import { fetchAgentChat } from './services';
import type { ChatMessage } from './types';

const MAX_STEPS = 12;

const AgentPage: React.FC = () => {
  const { currentProjectId } = useModel('project');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [sessionId, setSessionId] = useState<string>('');
  const seqRef = useRef<number>(0);

  const nextId = useCallback((): string => {
    seqRef.current += 1;
    return `m${seqRef.current}`;
  }, []);

  const handleSend = useCallback(
    async (question: string): Promise<void> => {
      const userMsg: ChatMessage = { id: nextId(), role: 'user', content: question };
      const placeholder: ChatMessage = { id: nextId(), role: 'assistant', content: '' };
      setMessages((prev) => [...prev, userMsg, placeholder]);
      setLoading(true);
      try {
        const data = await fetchAgentChat({ question, sessionId: sessionId || undefined, maxSteps: MAX_STEPS });
        if (data?.sessionId) {
          setSessionId(data.sessionId);
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
    [nextId, sessionId],
  );

  // 切项目: 清空会话重开 (会话与 project 上下文绑定)。
  useEffect(() => {
    setMessages([]);
    setSessionId('');
    seqRef.current = 0;
  }, [currentProjectId]);

  return (
    <PageContainer>
      <div className="flex flex-col h-700 bg-#ffffff rounded-6">
        <div className="flex-1 overflow-auto">
          <MessageList messages={messages} />
        </div>
        <Composer loading={loading} onSend={handleSend} />
      </div>
    </PageContainer>
  );
};

export default AgentPage;
