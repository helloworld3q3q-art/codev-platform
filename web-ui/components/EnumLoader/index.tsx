import { useModel } from '@umijs/max';
import type { ReactNode } from 'react';
import { useEffect } from 'react';

// 枚举数据加载组件
const EnumLoader: React.FC<{ children: ReactNode }> = ({ children }) => {
  const { loadEnums } = useModel('enum');
  useEffect(() => {
    // 在应用初始化时加载枚举数据
    loadEnums();
  }, []);

  return <>{children}</>;
};

export default EnumLoader;
