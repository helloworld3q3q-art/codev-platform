import { Bubble, Sender } from '@ant-design/x';
import type { BubbleListProps, BubbleProps } from '@ant-design/x';
import { XMarkdown } from '@ant-design/x-markdown';
import { Empty } from 'antd';
import { useCallback, useMemo, useState } from 'react';

import type { ChatMessage } from '../types';

// assistant 气泡正文走 Markdown 渲染(代码块 / 列表 / 表格)。模块级函数, 避免 jsx-no-bind。
const renderMarkdown: NonNullable<BubbleProps['contentRender']> = (content) => {
  return <XMarkdown>{String(content ?? '')}</XMarkdown>;
};

// 角色 → 气泡样式: user 右侧实心, assistant 左侧描边 + Markdown。模块级常量, 不随渲染重建。
const BUBBLE_ROLES: BubbleListProps['role'] = {
  user: { placement: 'end', variant: 'filled' },
  assistant: { placement: 'start', variant: 'outlined', contentRender: renderMarkdown },
};

interface ChatPanelProps {
  messages: ChatMessage[];
  loading: boolean;
  onSend: (question: string) => void;
}

const ChatPanel: React.FC<ChatPanelProps> = ({ messages, loading, onSend }) => {
  const [value, setValue] = useState<string>('');

  const items = useMemo<BubbleListProps['items']>(
    () =>
      messages.map((m) => ({
        key: m.id,
        role: m.role,
        content: m.content,
        loading: m.role === 'assistant' && !m.content, // 占位等待中显示 loading 点
      })),
    [messages],
  );

  const handleSubmit = useCallback(
    (msg: string): void => {
      const question = msg.trim();
      if (!question || loading) {
        return;
      }
      onSend(question);
      setValue('');
    },
    [loading, onSend],
  );

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-auto p-16">
        {messages.length ? (
          <Bubble.List items={items} role={BUBBLE_ROLES} autoScroll />
        ) : (
          <div className="flex items-center justify-center h-full">
            <Empty description="开始与 Agent 对话" />
          </div>
        )}
      </div>
      <div className="p-12 border-t border-#f0f0f0">
        <Sender
          value={value}
          loading={loading}
          placeholder="输入问题, Enter 发送 / Shift+Enter 换行"
          onChange={setValue}
          onSubmit={handleSubmit}
        />
      </div>
    </div>
  );
};

export default ChatPanel;
