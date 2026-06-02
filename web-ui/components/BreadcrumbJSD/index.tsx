import type { PageHeaderProps } from '@ant-design/pro-components';
import type { BreadcrumbProps } from 'antd';
import { Breadcrumb } from 'antd';
import 'antd/lib/breadcrumb/style';
import * as React from 'react';

/**
 * 禁用面包屑点击的通用组件
 * 用于在 PageContainer 中禁用面包屑的点击功能
 */
export const DisabledBreadcrumb: React.FC<{
  props: PageHeaderProps;
  defaultDom: React.ReactNode;
}> = ({ defaultDom }) => {
  return React.cloneElement(defaultDom as React.ReactElement, {
    itemRender: (route: { breadcrumbName?: string; title?: string }) => {
      return <span>{route.breadcrumbName || route.title}</span>;
    },
  });
};

/**
 * 禁用面包屑点击的 breadcrumbRender 函数
 * 可直接用于 PageContainer 的 breadcrumbRender 属性
 */
export const disabledBreadcrumbRender = (_props: PageHeaderProps, defaultDom: React.ReactNode) => {
  return React.cloneElement(defaultDom as React.ReactElement, {
    itemRender: (route: { breadcrumbName?: string; title?: string }) => {
      return <span>{route.breadcrumbName || route.title}</span>;
    },
  });
};

/**
 * 自定义面包屑组件
 * 可用于替代 Ant Design 的 Breadcrumb 组件
 */
export const CustomBreadcrumb: React.FC<
  BreadcrumbProps & {
    disableClick?: boolean;
  }
> = ({ disableClick = true, ...props }) => {
  if (disableClick) {
    return (
      <Breadcrumb
        {...props}
        itemRender={(route) => {
          return <span>{route.breadcrumbName || route.title}</span>;
        }}
      />
    );
  }
  return <Breadcrumb {...props} />;
};

export default {
  DisabledBreadcrumb,
  disabledBreadcrumbRender,
  CustomBreadcrumb,
};
