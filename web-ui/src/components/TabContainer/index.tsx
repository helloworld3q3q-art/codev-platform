import { findMenuNameByPath, MENU_ITEMS } from '@/menus';
import UserMenu from '@/components/UserMenu';
import { TabContainer as JlogiTabContainer } from '@jlogi/ui';
import { history, useKeepOutlets, useLocation } from '@umijs/max';
import React, { useCallback } from 'react';

// 页签栏右侧插槽：用户菜单(组织/项目切换 + 登出)。元素稳定, 提到组件外避免每次渲染重建。
const tabBarExtraContent = { right: <UserMenu /> };

// 排除的路径（根路径和空路径不展示在 tab 中）
export const excludePaths = ['/user/login', '/system/404', '/404', '/', ''];

// 需要根据 query 参数动态拼接标题的路由配置（如详情页带 id）
const routeTitleConfig: Record<
  string,
  {
    prefix: string;
    queryKey: string;
  }
> = {
  '/backtestdetail': {
    prefix: '回测详情',
    queryKey: 'runId',
  },
};

/**
 * Tab 容器包装组件
 *
 * 基于 @jlogi/ui 的 TabContainer 进行封装：
 * - 连接 umi 路由
 * - 按 path 在静态菜单 MENU_ITEMS 中反查中文 name 作为页签标题
 */
const TabContainer: React.FC = () => {
  const location = useLocation();
  const element = useKeepOutlets();

  // 默认首页路径
  const defaultPath = '/dashboard';

  // 从静态菜单按 path 反查标题；未命中时返回 pathname 兜底
  const getMenuTitle = useCallback((pathWithSearch: string): string => {
    try {
      const [pathname, search = ''] = pathWithSearch.split('?');

      // 详情类页面：用 query 参数拼标题，例如 "数据集-T123"
      const customRule = routeTitleConfig[pathname];
      if (customRule) {
        const queryValue = new URLSearchParams(search).get(customRule.queryKey)?.trim();
        return queryValue ? `${customRule.prefix}-${queryValue}` : customRule.prefix;
      }

      return findMenuNameByPath(MENU_ITEMS, pathname) ?? pathname;
    } catch {
      return pathWithSearch;
    }
  }, []);

  // 路由跳转函数
  const navigate = useCallback((path: string) => {
    history.push(path);
  }, []);

  return (
    <JlogiTabContainer
      pathname={`${location.pathname}${location.search || ''}`}
      navigate={navigate}
      defaultPath={defaultPath}
      excludePaths={excludePaths}
      getTitle={getMenuTitle}
      storageKey="stock-admin-web-tab-container"
      enableContextMenu={true}
      tabBarExtraContent={tabBarExtraContent}
    >
      {element}
    </JlogiTabContainer>
  );
};

export default TabContainer;
