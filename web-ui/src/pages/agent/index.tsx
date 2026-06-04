import PageContainer from '@/components/PageContainer';
import { useModel } from '@umijs/max';
import { useCallback, useEffect, useRef, useState } from 'react';

import { getMessages, getSessions, postAgentChat } from '@/services/apis/agentapi';

import ChatPanel from './components/ChatPanel';
import SessionSider from './components/SessionSider';
import type { ChatMessage } from './types';

const MAX_STEPS = 12;

// 轻量聚合页: 只持状态 + 编排回调(loadSessions / 新建 / 选择 / 发送), 业务逻辑下沉到生成接口 + 子组件。
const AgentPage: React.FC = () => {
  const { currentProjectId } = useModel('project');
  const [sessions, setSessions] = useState<API.SessionItem[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>('');
  const [loading, setLoading] = useState<boolean>(false);
  const seqRef = useRef<number>(0);
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
      const placeholder: ChatMessage = { id: nextId(), role: 'assistant', content: '' };
      setMessages((prev) => [...prev, userMsg, placeholder]);
      setLoading(true);
      try {
        const res = await postAgentChat({
          question,
          sessionId: sentFor || undefined, // 用 ref 记录的当前会话, 不读可能过期的 state 闭包
          maxSteps: MAX_STEPS,
        });
        if (activeSessionRef.current !== sentFor) {
          return; // 发送途中切到别的会话, 丢弃本次响应, 不污染当前视图
        }
        const data = res.data;
        if (data?.sessionId) {
          activeSessionRef.current = data.sessionId; // 新会话落定 id, 同步 ref
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
        if (activeSessionRef.current !== sentFor) {
          return;
        }
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === placeholder.id ? { ...msg, content: '请求失败, 请重试' } : msg,
          ),
        );
      } finally {
        if (activeSessionRef.current === sentFor) {
          setLoading(false);
        }
      }
    },
    [nextId, loadSessions],
  );

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
