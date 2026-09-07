import { useLocation } from '@umijs/max';
import { useEffect } from 'react';
import { createPortal } from 'react-dom';

// 菜单位换显面板: 打开时全局左侧菜单向左滑出隐藏, 本面板在其原位露出; 关闭时菜单滑回盖住。
// 只管"菜单 ↔ 面板"的位置互换, 面板内容由 children 提供。
// 面板 z 99 垫在菜单(ProLayout fixed sider z-index 100)之下、内容之上: 菜单滑走即露出, 自身无需动画。
// 几何/显隐走 inline style(同 DraggablePanel 模式): @unocss/webpack dev 期不热生成新类,
// 首次使用的工具类要重启 dev server 才有 CSS, 定位类缺失会让面板整个掉出视口。
const AGENT_PATH = '/agent';

const PANEL_STYLE: React.CSSProperties = {
  top: 0,
  bottom: 0,
  left: 0,
  width: 256, // 与 ProLayout 默认 siderWidth 对齐
  zIndex: 99,
  borderRight: '1px solid #f0f0f0',
  overflowY: 'auto', // 滚动在面板这层: 滚动条贴面板右缘, 不挤压内层列表; 内层头部用 sticky 钉顶
};

interface MenuSwapPanelProps {
  open: boolean;
  children: React.ReactNode;
}

const MenuSwapPanel: React.FC<MenuSwapPanelProps> = ({ open, children }) => {
  const { pathname } = useLocation();
  // 页面被 KeepAlive 缓存时仍挂载: 菜单位移/面板显隐必须按"激活路由是本页"门控, 防止切页签后泄漏。
  const active = pathname === AGENT_PATH;
  const showing = open && active;

  useEffect(() => {
    // ProLayout 侧边菜单是全局组件, 无法从页面内用类名控制, 故在此直接写 style(状态驱动的动态值)。
    const siders = document.querySelectorAll<HTMLElement>('.ant-pro-sider');
    siders.forEach((el) => {
      el.style.transition = 'transform 0.3s ease';
      el.style.transform = showing ? 'translateX(-100%)' : '';
    });
    return () => {
      siders.forEach((el) => {
        el.style.transform = '';
      });
    };
  }, [showing]);

  // Portal 挂 body: 脱离页面容器(KeepAlive/页签容器), fixed 以视口为参照。
  return createPortal(
    <div
      className="fixed bg-#ffffff"
      style={{
        ...PANEL_STYLE,
        opacity: showing ? 1 : 0,
        pointerEvents: showing ? 'auto' : 'none',
        // 收起: 等菜单滑回(0.3s)盖住后再隐藏, 避免闪空白; 展开: 立即可见
        transition: showing ? 'opacity 0s' : 'opacity 0s 0.3s',
      }}
    >
      {children}
    </div>,
    document.body,
  );
};

export default MenuSwapPanel;
