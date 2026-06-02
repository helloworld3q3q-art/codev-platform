import { App } from 'antd';
import React, { useEffect } from 'react';
import { setGlobalModal } from './index';

interface ModalProviderProps {
  children: React.ReactNode;
}

const ModalProvider: React.FC<ModalProviderProps> = ({ children }) => {
  const { modal } = App.useApp();

  useEffect(() => {
    // 设置全局modal实例
    setGlobalModal(modal);
  }, [modal]);

  return <>{children}</>;
};

export default ModalProvider;
