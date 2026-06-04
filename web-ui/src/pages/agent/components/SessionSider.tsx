import { PlusOutlined } from '@ant-design/icons';
import { Conversations } from '@ant-design/x';
import { Button, Empty } from 'antd';
import { useMemo } from 'react';

// 会话侧栏: 顶部"新建会话" + 历史会话列表。自洽组件, 只认 items + 两个回调。
interface SessionSiderProps {
  items: API.SessionItem[];
  activeKey: string;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
}

const SessionSider: React.FC<SessionSiderProps> = ({ items, activeKey, onSelect, onNew }) => {
  const convItems = useMemo(
    () =>
      items.map((s) => ({
        key: s.sessionId ?? '',
        label: s.title || '新会话',
      })),
    [items],
  );

  return (
    <div className="flex flex-col h-full w-280 bg-#fafafa border-r border-#f0f0f0">
      <div className="p-12">
        <Button type="primary" icon={<PlusOutlined />} block size="large" onClick={onNew}>
          新建会话
        </Button>
      </div>
      <div className="px-16 pb-6 text-12 text-#8c8c8c">历史会话</div>
      <div className="flex-1 overflow-auto px-8 pb-8">
        {convItems.length ? (
          <Conversations
            className="bg-transparent"
            items={convItems}
            activeKey={activeKey}
            onActiveChange={onSelect}
          />
        ) : (
          <div className="flex items-center justify-center h-160">
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史会话" />
          </div>
        )}
      </div>
    </div>
  );
};

export default SessionSider;
