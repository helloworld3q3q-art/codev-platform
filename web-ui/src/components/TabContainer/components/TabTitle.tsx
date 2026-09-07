import { CloseOutlined, ReloadOutlined } from '@ant-design/icons';
import { Dropdown, Tooltip } from 'antd';
import React, { useCallback, useMemo } from 'react';
import type { MenuClickInfo, TabActionEvent, TabActionType, TabItem } from './types';

/**
 * Tab 标题组件 Props
 */
interface TabTitleProps {
  tab: TabItem;
  tabs: TabItem[];
  isControlled: boolean;
  activeKey: string;
  enableContextMenu: boolean;
  onTabAction?: (event: TabActionEvent) => void;
  onInternalTabsChange: (tabs: TabItem[]) => void;
  onInternalActiveKeyChange: (key: string) => void;
}

/**
 * 获取右键菜单配置
 */
const getMenuItems = (tabsLength: number) => [
  { key: 'refresh', label: '刷新', icon: <ReloadOutlined /> },
  { type: 'divider' as const },
  { key: 'close', label: '关闭', disabled: tabsLength <= 1 },
  { key: 'closeOther', label: '关闭其他', disabled: tabsLength <= 1 },
  { key: 'closeRight', label: '关闭右侧' },
  { type: 'divider' as const },
  { key: 'closeAll', label: '关闭所有' },
];

/**
 * Tab 标题组件
 */
const TabTitle: React.FC<TabTitleProps> = (props) => {
  const {
    tab,
    tabs,
    isControlled,
    activeKey,
    enableContextMenu,
    onTabAction,
    onInternalTabsChange,
    onInternalActiveKeyChange,
  } = props;

  const handleTabClose = useCallback(
    (targetKey: string, e?: React.MouseEvent) => {
      e?.stopPropagation();
      if (!isControlled) {
        const newTabs = tabs.filter((t) => t.key !== targetKey);
        if (targetKey === activeKey && newTabs.length > 0) {
          const targetIndex = tabs.findIndex((item) => item.key === targetKey);
          const newActiveIndex = Math.min(targetIndex, newTabs.length - 1);
          onInternalActiveKeyChange(newTabs[newActiveIndex].key);
        }
        onInternalTabsChange(newTabs);
      }
      onTabAction?.({ type: 'close', key: targetKey });
    },
    [isControlled, activeKey, onTabAction, tabs, onInternalTabsChange, onInternalActiveKeyChange],
  );

  const handleMenuAction = useCallback(
    (info: MenuClickInfo) => {
      const actionType = info.key as TabActionType;
      if (!isControlled) {
        const actions: Record<TabActionType, () => void> = {
          close: () => handleTabClose(tab.key),
          closeOther: () => {
            onInternalTabsChange([tab]);
            onInternalActiveKeyChange(tab.key);
          },
          closeRight: () => {
            const targetIndex = tabs.findIndex((t) => t.key === tab.key);
            if (targetIndex !== -1) {
              onInternalTabsChange(tabs.slice(0, targetIndex + 1));
            }
          },
          closeAll: () => {
            // 关闭所有由外部 onTabAction 处理（保留首页逻辑在父组件），此处不做内部状态变更
          },
          refresh: () => {},
        };
        actions[actionType]?.();
      }
      onTabAction?.({
        type: actionType,
        key: actionType !== 'closeAll' ? tab.key : undefined,
      });
    },
    [
      isControlled,
      tab,
      tabs,
      onTabAction,
      handleTabClose,
      onInternalTabsChange,
      onInternalActiveKeyChange,
    ],
  );

  const menuItems = useMemo(() => getMenuItems(tabs.length), [tabs.length]);

  const handleCloseClick = useCallback(
    (e: React.MouseEvent) => {
      handleTabClose(tab.key, e);
    },
    [handleTabClose, tab.key],
  );

  const content = (
    <div className="group flex items-center gap-1 px-2">
      <Tooltip title={tab.title} mouseEnterDelay={0.5}>
        <span className="max-w-120 truncate mr-8">{tab.title}</span>
      </Tooltip>
      {tab.closable && tabs.length > 1 && (
        <CloseOutlined
          className="scale-80 text-12 hover:text-red-500 transition-transform duration-200 -rotate-90 group-hover:rotate-0"
          onClick={handleCloseClick}
        />
      )}
    </div>
  );

  if (!enableContextMenu) {
    return content;
  }

  return (
    <Dropdown menu={{ items: menuItems, onClick: handleMenuAction }} trigger={['contextMenu']}>
      {content}
    </Dropdown>
  );
};

export default TabTitle;
