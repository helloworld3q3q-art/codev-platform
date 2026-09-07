import type { PageHeaderProps as ProPageHeaderProps } from '@ant-design/pro-components';
import {
  PageContainer as JPageContainer,
  type PageContainerProps as JPageContainerProps,
} from '@jlogi/ui';
import { history } from '@umijs/max';
import { Route } from '@umijs/route-utils/dist/types';
import { Breadcrumb } from 'antd';
import React from 'react';

// 提到模块级，避免每次 render 创建新函数（react/jsx-no-bind）
const EMPTY_BREADCRUMB_RENDER = () => <></>;

interface Locations {
  pathname: string;
  hash: string;
  key: string;
  search: string;
  state: any;
}

// 使用原始的 ProPageHeaderProps 类型，避免类型冲突
interface CustomPageHeaderProps extends ProPageHeaderProps {
  location?: Locations;
  route?: Route;
}

interface PageContainerProps extends Omit<JPageContainerProps, 'breadcrumbRender'> {
  children?: React.ReactNode;
  /** 自定义面包屑渲染函数 */
  breadcrumbRender?:
    | false
    | ((props: ProPageHeaderProps, defaultDom: React.ReactNode) => React.ReactNode)
    | undefined;
}

const PageContainer: React.FC<PageContainerProps> = ({
  breadcrumbRender,
  breadcrumb,
  ...props
}) => {
  // 默认的面包屑渲染函数
  // 从 routes 中提取路由层级信息的函数
  const extractRouteHierarchy = (routes: Route[], targetPath: string) => {
    const hierarchy: { name: string; path: string; level: number }[] = [];
    // 确保 routes 是数组
    const routeArray: any[] = routes;
    const findRoute = (routeList: any[], level: number = 0) => {
      for (const route of routeList) {
        const currentPath = route.path;

        // 如果当前路径匹配目标路径或者是目标路径的前缀
        if (
          currentPath &&
          (targetPath === currentPath || targetPath.startsWith(currentPath + '/'))
        ) {
          // 检查是否已经存在相同路径的项，避免重复
          const existingItem = hierarchy.find((item) => item.path === currentPath);
          if (!existingItem) {
            hierarchy.push({
              name: route.name || route.id || 'Unknown',
              path: currentPath,
              level: level,
            });
          }
        }
        // 递归查找子路由
        if (route.routes && Array.isArray(route.routes)) {
          findRoute(route.routes, level + 1);
        }
        // // 也检查 children 属性
        // if (route.children && Array.isArray(route.children)) {
        // 	findRoute(route.children, level + 1);
        // }
      }
    };

    findRoute(routeArray);
    // 按层级排序
    return hierarchy.sort((a, b) => a.level - b.level);
  };

  const defaultBreadcrumbRender = (headerProps: ProPageHeaderProps) => {
    // 类型断言为包含自定义字段的类型
    const customProps = headerProps as CustomPageHeaderProps;
    const { location, route } = customProps;
    const { pathname } = location || {};
    // 从 routes 中提取路由层级信息
    if (route && pathname) {
      const routes = route?.routes || [];
      const routeHierarchy = extractRouteHierarchy(routes, pathname);
      // 如果找到了路由层级，可以用它来替代或补充现有的面包屑
      if (routeHierarchy.length > 0) {
        const breadcrumbItems = routeHierarchy.map((item, index) => {
          // 累积显示从第一个到当前层级的所有name
          const cumulativeName = routeHierarchy
            .slice(0, index + 1)
            .map((hierarchyItem) => hierarchyItem.name)
            .join('.');
          return {
            key: item.path,
            // locale 插件未启用（config.ts locale:false），直接用层级名作为面包屑标题
            title: cumulativeName,
          };
        });
        return <Breadcrumb items={breadcrumbItems} />;
      }
    }
    return null;
  };

  const defaultProps = {
    title: false,
    breadcrumbRender:
      breadcrumbRender ||
      ((headerProps: ProPageHeaderProps) => {
        if (breadcrumb && breadcrumb.items && breadcrumb.items.length > 0) {
          const breadcrumbItems = breadcrumb.items.map((item) => ({
            key: item.path || '',
            title: item.title,
            onClick: () => {
              const { path } = item;
              if (path) {
                history.push(path);
              }
            },
          }));
          return <Breadcrumb items={breadcrumbItems} />;
        }
        // headerProps 中已经包含 matchMenus 和 matchMenuKeys 字段
        return defaultBreadcrumbRender(headerProps);
      }),
    ...props,
  };

  return (
    <JPageContainer
      {...defaultProps}
      childrenContentStyle={{ paddingLeft: 10, paddingRight: 10 }}
      breadcrumbRender={EMPTY_BREADCRUMB_RENDER}
      header={{ style: { padding: 0, paddingTop: 6 } }}
    />
  );
};

export default PageContainer;
export type { PageContainerProps };
