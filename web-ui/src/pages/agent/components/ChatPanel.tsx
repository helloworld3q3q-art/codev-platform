import { BulbOutlined, CodeOutlined, ReadOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons';
import { Bubble, Prompts, Sender, Welcome } from '@ant-design/x';
import type { BubbleListProps, BubbleProps, PromptsProps } from '@ant-design/x';
import { XMarkdown } from '@ant-design/x-markdown';
import { Avatar } from 'antd';
import { useCallback, useMemo, useState } from 'react';

import type { ChatMessage } from '../types';

// assistant 气泡正文走 Markdown 渲染(代码块 / 列表 / 表格)。模块级函数, 避免 jsx-no-bind。
const renderMarkdown: NonNullable<BubbleProps['contentRender']> = (content) => {
  return <XMarkdown>{String(content ?? '')}</XMarkdown>;
};

// 角色头像(同角色复用同一实例)。模块级常量, 不随渲染重建。
const USER_AVATAR = <Avatar size={34} icon={<UserOutlined />} style={{ backgroundColor: '#7265e6' }} />;
const AI_AVATAR = <Avatar size={34} icon={<RobotOutlined />} style={{ backgroundColor: '#1677ff' }} />;

// 角色 → 气泡样式: user 右侧实心圆角, assistant 左侧描边圆角 + Markdown。
const BUBBLE_ROLES: BubbleListProps['role'] = {
  user: { placement: 'end', variant: 'filled', shape: 'round', avatar: USER_AVATAR },
  assistant: {
    placement: 'start',
    variant: 'outlined',
    shape: 'round',
    avatar: AI_AVATAR,
    contentRender: renderMarkdown,
  },
};

// 空会话引导:点击即发送(key 即问题原文)。
const PROMPT_ITEMS = [
  { key: '这个项目是做什么的?', icon: <BulbOutlined className="text-#faad14" />, label: '了解项目', description: '它的定位与核心能力' },
  { key: '帮我搜索 SessionStore 的定义和调用方', icon: <CodeOutlined className="text-#1677ff" />, label: '查代码', description: '符号定义 / 调用链 / 影响面' },
  { key: '有哪些跨层枚举一致性规则?', icon: <ReadOutlined className="text-#52c41a" />, label: '查规则文档', description: '规则 / 设计 / 事故复盘' },
];

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

  const handlePromptClick = useCallback<NonNullable<PromptsProps['onItemClick']>>(
    (info): void => {
      if (loading) {
        return;
      }
      onSend(String(info.data.key));
    },
    [loading, onSend],
  );

  return (
    <div className="flex flex-col h-full bg-#ffffff">
      <div className="flex-1 min-h-0">
        {messages.length ? (
          <Bubble.List className="h-full px-24 py-20" items={items} role={BUBBLE_ROLES} autoScroll />
        ) : (
          <div className="h-full flex flex-col items-center justify-center gap-28 px-24">
            <Welcome
              variant="borderless"
              icon={<RobotOutlined className="text-40 text-#1677ff" />}
              title="AI 助手"
              description="基于平台 代码图谱 / 文档检索 / 跨层链路, 帮你查代码、查规则、理解项目。"
            />
            <Prompts title="试试这样问 👇" items={PROMPT_ITEMS} wrap onItemClick={handlePromptClick} />
          </div>
        )}
      </div>
      <div className="px-16 py-12 border-t border-#f0f0f0">
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
