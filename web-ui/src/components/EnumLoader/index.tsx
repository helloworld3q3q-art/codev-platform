import { useModel } from '@umijs/max';
import type { ReactNode } from 'react';
import { useEffect } from 'react';

// 枚举数据加载组件：未登录时不请求受保护接口。
const EnumLoader: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { loadEnums } = useModel('enum');
  useEffect(() => {
    const token = localStorage.getItem('auth_token');
    if (!token) return;
    loadEnums();
  }, [loadEnums]);

  return <>{children}</>;
};

export default EnumLoader;
