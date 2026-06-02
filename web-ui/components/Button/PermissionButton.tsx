// import { usePermissions } from '@/models/permissions';
// import { useLocation } from '@umijs/max';
import { Button, ButtonProps } from 'antd';
import React from 'react';

interface PermissionButtonProps extends ButtonProps {
  /** 路由路径（可选，默认使用当前浏览器地址） */
  path?: string;
  /** 权限码 */
  permissionCode?: string;
  /** 无权限时是否隐藏按钮（默认隐藏） */
  hideWhenNoPermission?: boolean;
}

/**
 * 权限按钮组件
 */
const PermissionButton: React.FC<PermissionButtonProps> = ({
  // path, permissionCode, hideWhenNoPermission = true, — reserved for permission check
  children,
  ...buttonProps
}) => {
  // const location = useLocation();
  // const { hasPermission } = usePermissions();

  // 如果没有传入 path，则使用当前浏览器地址
  //const currentPath = path || location.pathname;

  //  const hasCurrentPermission = hasPermission(currentPath, permissionCode);

  // 如果没有权限且设置为隐藏，则返回null
  // if (!hasCurrentPermission && hideWhenNoPermission) {
  //   return null;
  // }

  // return (
  //   <Button size="small" {...buttonProps} disabled={!hasCurrentPermission || buttonProps.disabled}>
  //     {children}
  //   </Button>
  // );
  return (
    <Button size="middle" {...buttonProps}>
      {children}
    </Button>
  );
};

export default PermissionButton;
