/**
 * UnoCSS 快捷方式配置
 * 定义常用的样式组合
 *
 * 注意：baseFontSize: 4，所以 px-4 = 4px，px-16 = 16px
 * 部分快捷方式内部引用了同名类（如 flex-col、text-ellipsis），
 * UnoCSS 自动检测递归并跳过快捷方式，内部类名回退到 presetWind3 处理
 */
export const shortcuts: Record<string, string> = {
  // 按钮样式（px-16 = 16px, py-8 = 8px）
  'btn-primary': 'bg-primary text-white px-16 py-8 rounded-4',
  'btn-secondary': 'bg-secondary text-white px-16 py-8 rounded-4',

  // Flex 布局
  'flex-center': 'flex items-center justify-center',
  'flex-between': 'flex items-center justify-between',
  'flex-col': 'flex flex-col',
  'flex-col-center': 'flex flex-col items-center justify-center',
  'flex-right': 'flex justify-end',
  'flex-col-right': 'flex flex-col items-end',

  // Grid 布局
  'grid-center': 'grid place-items-center',

  // 定位（使用 Wind3 分数语法，translate 正确组合 X+Y）
  'absolute-center': 'absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2',
  'absolute-full': 'absolute top-0 left-0 w-full h-full',

  // 卡片
  card: 'bg-white rounded-8 shadow-4 p-16',

  // 输入框
  'input-base': 'border-1 rounded-4 px-12 py-8 w-full focus:outline-none',

  // 文本处理（内部 text-ellipsis 递归时回退到 Wind3 的 text-overflow: ellipsis）
  'text-ellipsis': 'whitespace-nowrap overflow-hidden text-ellipsis',
  'truncate-1': 'overflow-hidden text-ellipsis whitespace-nowrap',
  'truncate-2': 'overflow-hidden text-ellipsis line-clamp-2',
  'truncate-3': 'overflow-hidden text-ellipsis line-clamp-3',

  // 文本大小（覆盖 Wind3 的相对尺寸，使用固定 px 值）
  'text-sm': 'text-14 leading-20',
  'text-base': 'text-16 leading-24',
  'text-lg': 'text-18 leading-28',
  'text-xl': 'text-20 leading-30',
  'text-2xl': 'text-24 leading-36',

  // 交互效果
  'hover-scale': 'transition-transform duration-300 hover:scale-105',
  'hover-shadow': 'transition-shadow duration-300 hover:shadow-4',
  clickable: 'cursor-pointer hover:opacity-80 transition-opacity duration-200',
};
