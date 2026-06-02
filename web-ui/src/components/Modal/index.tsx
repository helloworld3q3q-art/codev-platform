import { Modal as JModal, type ModalProps } from '@jlogi/ui';
import React from 'react';

export { showConfirm, showError, showInfo, showSuccess } from '@jlogi/ui';
export type { ModalProps };

const Modal: React.FC<ModalProps> = ({ children, ...restProps }) => {
  return <JModal {...restProps}>{children}</JModal>;
};

export default Modal;
