import type { MenuItem } from '@/services/login';
import { i18nMessages } from '@/utils/i18n';
import { TabContainer as JlogiTabContainer } from '@jlogi/ui';
import { history, useKeepOutlets, useLocation, useModel } from '@umijs/max';
import React, { useCallback } from 'react';

// 排除的路径（根路径和空路径不展示在 tab 中）
export const excludePaths = ['/user/login', '/system/404', '/404', '/', ''];

// 需要自定义页签标题的路由配置
const routeTitleConfig: Record<
  string,
  {
    prefix: string;
    queryKey: string;
  }
> = {
  '/datasets/management/detail': {
    prefix: '数据集',
    queryKey: 'taskNo',
  },
};

/**
 * Tab 容器包装组件
 *
 * 基于 @jlogi/ui 的 TabContainer 进行封装
 * 连接 umi model 和路由，处理国际化和缓存清理
 */
const TabContainer: React.FC = () => {
  const location = useLocation();
  const element = useKeepOutlets();
  const { userInfo } = useModel('user');

  // 默认首页路径
  const defaultPath = '/welcome';

  // 同步 ProLayout 的 i18n 翻译逻辑：menu.{parentName}.{name} 作为 i18n key
  const getMenuTitle = useCallback(
    (pathWithSearch: string): string => {
      try {
        const [pathname, search = ''] = pathWithSearch.split('?');
        const customTitleRule = routeTitleConfig[pathname];
        if (customTitleRule) {
          const searchParams = new URLSearchParams(search);
          const queryValue = searchParams.get(customTitleRule.queryKey)?.trim();
          if (queryValue) {
            return `${customTitleRule.prefix}-${queryValue}`;
          }
          return customTitleRule.prefix;
        }

        const menus: MenuItem[] = userInfo.menus || [];
        const findMenuName = (
          menuList: MenuItem[],
          path: string,
          keyPrefix: string,
        ): string | null => {
          for (const menu of menuList) {
            const currentKey = `${keyPrefix}.${menu.menuName}`;
            if (menu.path === path) return i18nMessages(currentKey, menu.menuName ?? path);
            if (menu.children?.length) {
              const name = findMenuName(menu.children, path, currentKey);
              if (name) return name;
            }
          }
          return null;
        };
        return findMenuName(menus, pathname, 'menu') || pathWithSearch;
      } catch {
        return pathWithSearch;
      }
    },
    [userInfo.menus],
  );

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
      storageKey="ai-context-support-web-tab-container"
      enableContextMenu={true}
    >
      {element}
    </JlogiTabContainer>
  );
};

export default TabContainer;
