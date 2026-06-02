import { theme as antdThemeAlgorithm, type ThemeConfig } from 'antd';

/**
 * 深空科技主题（Deep Space Tech）
 *
 * - colorPrimary indigo-500，搭配 slate 深色侧边栏与发光阴影
 * - 金融数据展示场景下保留 success/error/warning 默认色，避免破坏涨跌色直觉
 * - 圆角统一 8，控件偏大但留白克制
 */
const antdTheme: ThemeConfig = {
  algorithm: antdThemeAlgorithm.defaultAlgorithm,
  token: {
    // 主色：indigo
    colorPrimary: '#6366f1',
    colorLink: '#6366f1',
    colorLinkHover: '#818cf8',
    colorLinkActive: '#4f46e5',

    // 文本
    colorText: '#0f172a',
    colorTextSecondary: '#475569',
    colorTextTertiary: '#94a3b8',

    // 边框
    colorBorder: '#e2e8f0',
    colorBorderSecondary: '#f1f5f9',

    // 背景
    colorBgLayout: '#f8fafc',
    colorBgContainer: '#ffffff',
    colorBgElevated: '#ffffff',
    colorSuccess: '#16a34a',
    colorSuccessBg: '#ecfdf3',
    colorSuccessBorder: '#bbf7d0',
    colorSuccessHover: '#22c55e',
    colorSuccessActive: '#15803d',
    colorSuccessText: '#166534',
    colorSuccessTextHover: '#166534',
    colorSuccessTextActive: '#14532d',
    colorError: '#d94841',
    colorErrorBg: '#fef2f2',
    colorErrorBorder: '#fecaca',
    colorErrorHover: '#ef4444',
    colorErrorActive: '#b91c1c',
    colorErrorText: '#991b1b',
    colorErrorTextHover: '#991b1b',
    colorErrorTextActive: '#7f1d1d',
    colorWarning: '#d97706',
    colorWarningBg: '#fff7ed',
    colorWarningBorder: '#fed7aa',
    colorWarningHover: '#f59e0b',
    colorWarningActive: '#c2410c',
    colorWarningText: '#9a3412',
    colorWarningTextHover: '#9a3412',
    colorWarningTextActive: '#7c2d12',
    colorInfo: '#2563eb',
    colorInfoBg: '#eff6ff',
    colorInfoBorder: '#bfdbfe',
    colorInfoHover: '#3b82f6',
    colorInfoActive: '#1d4ed8',
    colorInfoText: '#1d4ed8',
    colorInfoTextHover: '#1d4ed8',
    colorInfoTextActive: '#1e40af',

    // 圆角与阴影：圆角偏大、阴影柔和
    borderRadius: 8,
    borderRadiusLG: 12,
    borderRadiusSM: 6,
    boxShadow: '0 1px 2px rgba(15, 23, 42, 0.04), 0 1px 3px rgba(15, 23, 42, 0.06)',
    boxShadowSecondary: '0 4px 12px rgba(99, 102, 241, 0.12), 0 2px 4px rgba(15, 23, 42, 0.04)',

    // 字号
    fontSize: 14,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif',

    // 控件高度
    controlHeight: 34,
    controlHeightLG: 40,
    controlHeightSM: 28,
  },
  components: {
    Layout: {
      // ProLayout 头部背景由 global.less 用渐变覆盖；这里仅给默认值兜底
      headerBg: '#0f172a',
      headerColor: '#e2e8f0',
      bodyBg: '#f8fafc',
      triggerBg: '#1e293b',
      triggerColor: '#e2e8f0',
    },
    Menu: {
      // light 模式二级展开子菜单背景（slate-50，比白色主体略深一档，区分层级不突兀）
      subMenuItemBg: '#f8fafc',
      // 左侧暗色菜单备用（navTheme=dark 时生效）
      darkItemBg: '#0f172a',
      darkSubMenuItemBg: '#0f172a',
      darkPopupBg: '#1e293b', // 收起后悬浮弹出菜单的背景（slate-800）
      darkItemColor: '#818cf8', // 未选中：indigo-400
      darkItemHoverColor: '#a5b4fc', // hover：indigo-300
      darkItemHoverBg: 'rgba(99, 102, 241, 0.18)',
      darkItemSelectedBg: 'rgba(99, 102, 241, 0.32)',
      darkItemSelectedColor: '#6366f1', // 选中：indigo-500
      iconSize: 16,
      itemHeight: 40,
    },
    Table: {
      headerBg: '#f8fafc',
      headerColor: '#475569',
      headerBorderRadius: 8,
      borderColor: '#f1f5f9',
    },
    Card: {
      borderRadiusLG: 12,
      headerBg: 'transparent',
    },
    Button: {
      borderRadius: 8,
      controlHeight: 34,
      // primary 按钮加 indigo 发光阴影
      primaryShadow: '0 2px 4px rgba(99, 102, 241, 0.25)',
    },
    Input: {
      borderRadius: 8,
      activeShadow: '0 0 0 2px rgba(99, 102, 241, 0.18)',
    },
    Select: {
      borderRadius: 8,
    },
    Tag: {
      borderRadiusSM: 6,
      defaultBg: '#f8fafc',
      defaultColor: '#475569',
      solidTextColor: '#ffffff',
    },
    Statistic: {
      // 仪表盘大数字字号增强
      contentFontSize: 30,
    },
  },
};

export default antdTheme;
