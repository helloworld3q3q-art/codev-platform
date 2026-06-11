import { PlusOutlined } from '@ant-design/icons';
import { Conversations } from '@ant-design/x';
import { Button, Empty } from 'antd';
import { useMemo } from 'react';

// 会话面板内容: 新建会话 + 历史列表。外层容器/宽度由 MenuSwapPanel 提供, 本组件只管内容。
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
    <div>
      {/* 滚动在外层 MenuSwapPanel; 头部 sticky 钉顶, 列表从其下方滚过 */}
      <div
        className="flex flex-col gap-8 p-12 bg-#ffffff"
        style={{ position: 'sticky', top: 0, zIndex: 1 }}
      >
        <Button type="primary" icon={<PlusOutlined />} block onClick={onNew}>
          新建会话
        </Button>
        <div className="text-12 text-#8c8c8c">历史会话</div>
      </div>
      <div style={{ padding: '0 12px 12px' }}>
        {convItems.length ? (
          <Conversations
            className="bg-transparent"
            items={convItems}
            activeKey={activeKey}
            onActiveChange={onSelect}
          />
        ) : (
          <div className="flex items-center justify-center py-24">
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史会话" />
          </div>
        )}
      </div>
    </div>
  );
};

export default SessionSider;
