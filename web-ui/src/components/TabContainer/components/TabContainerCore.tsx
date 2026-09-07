import { Tabs } from 'antd';
import React, { useCallback, useState } from 'react';
import TabTitle from './TabTitle';
import type { TabContainerCoreProps, TabItem } from './types';

/**
 * Tab 容器核心组件
 *
 * 支持受控/非受控模式：
 * - 外部传入 tabs/activeKey 时：受控模式，使用外部数据
 * - 外部不传时：非受控模式，使用内部状态
 */
const TabContainerCore: React.FC<TabContainerCoreProps> = ({
  tabs: externalTabs,
  activeKey: externalActiveKey,
  children,
  onTabChange,
  onTabAction,
  enableContextMenu = true,
  className = '',
}) => {
  const [internalTabs, setInternalTabs] = useState<TabItem[]>([]);
  const [internalActiveKey, setInternalActiveKey] = useState<string>('');

  const isControlled = externalTabs !== undefined;
  const tabs = isControlled ? externalTabs : internalTabs;
  const activeKey = isControlled ? externalActiveKey : internalActiveKey;

  const handleTabChange = useCallback(
    (key: string) => {
      if (!isControlled) {
        setInternalActiveKey(key);
      }
      onTabChange?.(key);
    },
    [isControlled, onTabChange],
  );

  if (tabs.length === 0) {
    return <>{children}</>;
  }

  return (
    <div className={`flex flex-col h-full ${className}`}>
      <div className="bg-white border-b border-gray-200 px-4 pt-8">
        <Tabs
          className="[&_.ant-tabs-nav]:mb-0"
          activeKey={activeKey}
          onChange={handleTabChange}
          type="card"
          size="small"
          items={tabs.map((tab) => ({
            key: tab.key,
            label: (
              <TabTitle
                tab={tab}
                tabs={tabs}
                isControlled={isControlled}
                activeKey={activeKey ?? ''}
                enableContextMenu={enableContextMenu}
                onTabAction={onTabAction}
                onInternalTabsChange={setInternalTabs}
                onInternalActiveKeyChange={setInternalActiveKey}
              />
            ),
            closable: tab.closable,
          }))}
          style={{ marginBottom: 0 }}
        />
      </div>
      <div className="flex-1 overflow-auto">{children}</div>
    </div>
  );
};

export default TabContainerCore;
