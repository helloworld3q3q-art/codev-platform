import { history } from '@umijs/max';
import { useCallback, useEffect, useState } from 'react';

const TABS_STORAGE_KEY = 'tabs_cache';
const ACTIVE_KEY_STORAGE_KEY = 'active_tab_key';

export interface TabItem {
  key: string; // 路由路径
  title: string; // tab标题
  closable?: boolean; // 是否可关闭
}

/**
 * 从localStorage读取缓存的tabs
 * 会过滤掉根路径(/)和空路径的 tab
 */
function loadTabsFromStorage(): { tabs: TabItem[]; activeKey: string } {
  try {
    const tabsStr = localStorage.getItem(TABS_STORAGE_KEY);
    const activeKeyStr = localStorage.getItem(ACTIVE_KEY_STORAGE_KEY);

    if (tabsStr) {
      const allTabs = JSON.parse(tabsStr) as TabItem[];
      const tabs = allTabs.filter((tab) => tab.key !== '/' && tab.key !== '');
      const activeKey =
        activeKeyStr && activeKeyStr !== '/' && activeKeyStr !== '' ? activeKeyStr : '';
      return { tabs, activeKey };
    }
  } catch (error) {
    console.error('读取tabs缓存失败:', error);
  }
  return { tabs: [], activeKey: '' };
}

/**
 * 保存tabs到localStorage
 */
function saveTabsToStorage(tabs: TabItem[], activeKey: string): void {
  try {
    localStorage.setItem(TABS_STORAGE_KEY, JSON.stringify(tabs));
    localStorage.setItem(ACTIVE_KEY_STORAGE_KEY, activeKey);
  } catch (error) {
    console.error('保存tabs缓存失败:', error);
  }
}

/**
 * Tab管理Model
 */
export default function useTabContainer() {
  const [tabs, setTabs] = useState<TabItem[]>(() => loadTabsFromStorage().tabs);
  const [activeKey, setActiveKey] = useState<string>(() => loadTabsFromStorage().activeKey);
  // 默认首页路径
  const defaultPath = '/welcome';

  useEffect(() => {
    saveTabsToStorage(tabs, activeKey);
  }, [tabs, activeKey]);

  /**
   * 添加tab（仅新增，已存在则跳过）
   */
  const addTab = useCallback((tab: TabItem) => {
    setTabs((prevTabs) => {
      const exists = prevTabs.find((t) => t.key === tab.key);
      if (exists) return prevTabs;
      return [...prevTabs, tab];
    });
    setActiveKey(tab.key);
  }, []);

  /**
   * 设置tab（存在则更新属性，不存在则新增）
   * tabs > 1 时首页可关闭，否则不可关闭
   */
  const setTab = useCallback((tab: TabItem) => {
    setTabs((prevTabs) => {
      const exists = prevTabs.find((t) => t.key === tab.key);
      const newTabs = exists
        ? prevTabs.map((t) => (t.key === tab.key ? { ...t, ...tab } : t))
        : [...prevTabs, tab];
      const welcomeClosable = newTabs.length > 1;
      return newTabs.map((t) => (t.key === defaultPath ? { ...t, closable: welcomeClosable } : t));
    });
    setActiveKey(tab.key);
  }, []);

  /**
   * 更新当前激活的tab
   */
  const setActiveTab = useCallback((key: string) => {
    setActiveKey(key);
  }, []);

  /**
   * 关闭tab，导航由内部处理
   */
  const removeTab = useCallback(
    (targetKey: string) => {
      setTabs((prevTabs) => {
        const targetIndex = prevTabs.findIndex((tab) => tab.key === targetKey);
        if (targetIndex === -1) return prevTabs;

        const newTabs = prevTabs.filter((tab) => tab.key !== targetKey);

        if (activeKey === targetKey) {
          if (newTabs.length > 0) {
            const newActiveIndex = Math.min(targetIndex, newTabs.length - 1);
            setActiveKey(newTabs[newActiveIndex].key);
            history.push(newTabs[newActiveIndex].key);
          } else {
            setActiveKey(defaultPath);
            history.push(defaultPath);
          }
        }

        return newTabs;
      });
    },
    [activeKey],
  );

  /**
   * 关闭其他tab，导航到目标 tab
   */
  const closeOtherTabs = useCallback((targetKey: string) => {
    setTabs((prevTabs) => prevTabs.filter((tab) => tab.key === targetKey));
    setActiveKey(targetKey);
    history.push(targetKey);
  }, []);

  /**
   * 关闭右侧tab，若当前激活 tab 被关闭则导航到目标 tab
   */
  const closeRightTabs = useCallback(
    (targetKey: string) => {
      setTabs((prevTabs) => {
        const targetIndex = prevTabs.findIndex((tab) => tab.key === targetKey);
        if (targetIndex === -1) return prevTabs;

        const activeIndex = prevTabs.findIndex((tab) => tab.key === activeKey);
        if (activeIndex > targetIndex) {
          setActiveKey(targetKey);
          history.push(targetKey);
        }

        return prevTabs.slice(0, targetIndex + 1);
      });
    },
    [activeKey],
  );

  /**
   * 关闭所有tab（登出时使用）
   */
  const closeAllTabs = useCallback((): void => {
    setTabs([]);
    setActiveKey('');
  }, []);

  /**
   * 设置tabs并导航到第一个 tab（用于关闭所有并重置到首页）
   */
  const setTabsList = useCallback((newTabs: TabItem[]) => {
    setTabs(newTabs);
    if (newTabs.length > 0) {
      setActiveKey(newTabs[0].key);
      history.push(newTabs[0].key);
    }
  }, []);

  return {
    tabs,
    activeKey,
    addTab,
    setTab,
    setActiveTab,
    removeTab,
    closeOtherTabs,
    closeRightTabs,
    closeAllTabs,
    setTabsList,
  };
}
