import { Drawer as JDrawer, type DrawerProps } from '@jlogi/ui';
import React from 'react';

const Drawer: React.FC<DrawerProps> = ({ children, ...restProps }) => {
  return <JDrawer {...restProps}>{children}</JDrawer>;
};

export default Drawer;
export type { DrawerProps };

// import { i18nMessages } from '@/utils/i18n';
// import type { DrawerProps as AntdDrawerProps } from 'antd';
// import { Button, Drawer as AntdDrawer } from 'antd';
// import React, { useState } from 'react';
// import styles from './index.less';

// interface DrawerProps extends AntdDrawerProps {
//   /** 是否显示关闭按钮 */
//   showCloseButton?: boolean;
//   /** 自定义关闭按钮文本 */
//   closeButtonText?: string;
//   /** 是否显示遮罩层 */
//   mask?: boolean;
//   /** 点击遮罩层是否关闭 */
//   maskClosable?: boolean;
//   /** 是否显示右上角关闭按钮 */
//   closable?: boolean;
//   /** 自定义底部内容 */
//   footer?: React.ReactNode;
//   /** 提交事件 */
//   onSubmit?: () => Promise<boolean | void>;
//   onClose?: (e: React.MouseEvent | React.KeyboardEvent) => void;
//   title?: React.ReactNode;
// }

// const Drawer: React.FC<DrawerProps> = ({
//   children,
//   title,
//   open,
//   onClose,
//   width = 520,
//   placement = 'right',
//   mask = true,
//   maskClosable = true,
//   closable = true,
//   // showCloseButton = false,
//   // closeButtonText = '关闭',
//   footer,
//   onSubmit,
//   ...restProps
// }) => {
//   const [loading, setLoading] = useState(false);
//   const close = (e: React.MouseEvent | React.KeyboardEvent) => {
//     if (onClose) {
//       onClose(e);
//     }
//   };
//   const handleSubmit = async (e: React.MouseEvent<HTMLButtonElement>) => {
//     if (onSubmit) {
//       try {
//         setLoading(true);
//         const res = await onSubmit();
//         if (res === undefined) {
//           close(e);
//           return;
//         }
//       } finally {
//         setLoading(false);
//       }
//     } else {
//       close(e);
//     }
//   };

//   const innerFooter = (
//     <div className="flex-right">
//       <Button className="mr-8" onClick={close}>
//         {i18nMessages('cancel', '取消')}
//       </Button>
//       <Button type="primary" onClick={handleSubmit} loading={loading}>
//         {i18nMessages('save', '保存')}
//       </Button>
//     </div>
//   );

//   const innerTitle = <div className="flex">{title}</div>;

//   return (
//     <AntdDrawer
//       title={title ?? innerTitle}
//       open={open}
//       onClose={close}
//       size={width}
//       placement={placement}
//       mask={mask}
//       maskClosable={maskClosable}
//       closable={closable}
//       footer={footer ?? innerFooter}
//       {...restProps}
//       className={`${styles.drawer} ${restProps.className ? restProps.className : ''}`}
//     >
//       {children}
//     </AntdDrawer>
//   );
// };

// export default Drawer;
// export type { DrawerProps };
