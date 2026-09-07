/**
 * 菜单点击信息
 */
export interface MenuClickInfo {
  key: string;
}

/**
 * Tab项配置
 */
export interface TabItem {
  key: string; // 唯一标识，通常是路由路径
  title: string; // tab标题
  closable?: boolean; // 是否可关闭
}

/**
 * Tab操作类型
 */
export type TabActionType = 'close' | 'closeOther' | 'closeRight' | 'closeAll' | 'refresh';

/**
 * Tab操作事件
 */
export interface TabActionEvent {
  type: TabActionType;
  key?: string; // 目标tab key，closeAll 时为空
}

/**
 * Tab容器核心组件Props（支持受控/非受控模式）
 */
export interface TabContainerCoreProps {
  /** tab列表（可选，不传则使用内部缓存） */
  tabs?: TabItem[];
  /** 当前激活的tab key（可选，不传则使用内部缓存） */
  activeKey?: string;
  /** 子元素 */
  children: React.ReactNode;
  /** tab切换回调 */
  onTabChange?: (key: string) => void;
  /** tab操作回调（关闭、关闭其他、关闭右侧、关闭所有、刷新） */
  onTabAction?: (event: TabActionEvent) => void;
  /** 是否启用右键菜单，默认 true */
  enableContextMenu?: boolean;
  /** 自定义类名 */
  className?: string;
}
