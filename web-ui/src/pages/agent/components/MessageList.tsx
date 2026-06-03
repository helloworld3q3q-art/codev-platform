import { Collapse, Empty, Spin, Typography } from 'antd';
import { useMemo } from 'react';

import type { ChatMessage } from '../types';

interface MessageItemProps {
  message: ChatMessage;
}

// 单条消息气泡 —— 提取为子组件, 避免父级 .map 闭包内联 (react/jsx-no-bind)。
const MessageItem: React.FC<MessageItemProps> = ({ message }) => {
  const isUser = message.role === 'user';
  const usageText = useMemo(() => {
    if (!message.usage) {
      return '';
    }
    return Object.entries(message.usage)
      .map((entry) => `${entry[0]}: ${entry[1]}`)
      .join('  ');
  }, [message.usage]);

  const stepItems = useMemo(() => {
    const steps = message.steps ?? [];
    return [
      {
        key: 'steps',
        label: `工具调用轨迹 (${steps.length} 步)`,
        children: (
          <div className="flex flex-col gap-8">
            {steps.map((step) => (
              <div key={step.n} className="p-8 bg-#f5f5f5 rounded-6">
                <div className="text-12 text-#8c8c8c">
                  #{step.n} {step.tool ? `· ${step.tool}` : ''}
                </div>
                {step.thought ? (
                  <div className="mt-4 whitespace-pre-wrap break-all">{step.thought}</div>
                ) : null}
                {step.resultSummary ? (
                  <div className="mt-4 text-12 text-#595959 whitespace-pre-wrap break-all">
                    {step.resultSummary}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ),
      },
    ];
  }, [message.steps]);

  return (
    <div className={isUser ? 'flex justify-end' : 'flex justify-start'}>
      <div className={isUser ? 'max-w-720 ml-48' : 'max-w-720 mr-48'}>
        <div
          className={
            isUser
              ? 'p-12 rounded-6 bg-#e6f4ff whitespace-pre-wrap break-all'
              : 'p-12 rounded-6 bg-#f5f5f5 whitespace-pre-wrap break-all'
          }
        >
          {message.content || <Spin size="small" />}
        </div>
        {!isUser && (message.steps?.length || usageText) ? (
          <div className="mt-8">
            {message.steps?.length ? <Collapse ghost size="small" items={stepItems} /> : null}
            {usageText ? (
              <Typography.Text className="text-12 text-#8c8c8c">{usageText}</Typography.Text>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
};

interface MessageListProps {
  messages: ChatMessage[];
}

const MessageList: React.FC<MessageListProps> = ({ messages }) => {
  if (!messages.length) {
    return (
      <div className="flex items-center justify-center h-full">
        <Empty description="开始与 Agent 对话" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-16 p-16">
      {messages.map((message) => (
        <MessageItem key={message.id} message={message} />
      ))}
    </div>
  );
};

export default MessageList;
