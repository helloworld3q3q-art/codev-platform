import type { Rule } from 'unocss';
import { parseColor } from './utils';

/**
 * 创建 UnoCSS 自定义规则
 *
 * @param colors - 颜色配置对象（同时传入 theme.colors，presetWind3 自动处理命名颜色）
 *
 * ## 设计原则
 *
 * 本文件只定义 **presetWind3 不支持** 的规则，避免冲突：
 *
 * | 功能              | presetWind3 支持      | 本文件支持           |
 * |-------------------|----------------------|---------------------|
 * | 命名颜色          | ✅ text-primary      | ❌（交给 presetWind3）|
 * | 颜色透明度        | ✅ text-primary/50   | ❌（交给 presetWind3）|
 * | 内联 HEX 颜色     | ❌                   | ✅ text-#333         |
 * | 百分比单位 *p     | ❌（使用 w-1/2）     | ✅ w-50p             |
 * | 自定义阴影        | ❌                   | ✅ shadow-10-primary |
 * | Transform %       | ❌                   | ✅ translate-x-10p   |
 * | 渐变背景          | ✅ bg-gradient-to-r  | ✅ bg-gradient-primary-secondary |
 *
 * ## 使用示例
 *
 * ### 1. 内联 HEX 颜色
 * ```tsx
 * // 文本颜色
 * <span className="text-#333">深灰文本</span>
 * <span className="text-#FF5500">橙色文本</span>
 * <span className="text-#00000080">带透明度的黑色</span>
 *
 * // 背景颜色
 * <div className="bg-#f5f5f5">浅灰背景</div>
 * <div className="bg-#00F2FE">青色背景</div>
 *
 * // 边框颜色
 * <div className="border border-#f0f0f0">浅灰边框</div>
 * <div className="border-color-#333">深灰边框</div>
 * ```
 *
 * ### 2. 百分比单位（后缀 p 表示 %）
 * ```tsx
 * // 宽高
 * <div className="w-50p h-100p">宽度50%，高度100%</div>
 * <div className="max-w-80p min-h-50p">最大宽80%，最小高50%</div>
 *
 * // 位置
 * <div className="top-25p left-50p">top: 25%, left: 50%</div>
 *
 * // 内边距
 * <div className="p-5p pt-10p px-20p">各方向内边距</div>
 *
 * // 外边距
 * <div className="m-10p mt-5p mx-auto">各方向外边距</div>
 *
 * // 行高
 * <p className="leading-150p">行高150%</p>
 * ```
 *
 * ### 3. 自定义阴影
 * ```tsx
 * // 单参数：仅大小（使用默认阴影颜色）
 * <div className="shadow-10">0 5px 10px shadowColor</div>
 * <div className="shadow-20">0 10px 20px shadowColor</div>
 *
 * // 双参数：大小 + 颜色
 * <div className="shadow-10-primary">0 5px 10px primary</div>
 * <div className="shadow-15-#00000040">0 7.5px 15px rgba(0,0,0,0.25)</div>
 *
 * // 四参数：x偏移 y偏移 模糊度 颜色
 * <div className="shadow-2-4-8-primary">2px 4px 8px primary</div>
 *
 * // 五参数：x偏移 y偏移 模糊度 扩散度 颜色
 * <div className="shadow-0-4-8-0-primary">0 4px 8px 0 primary</div>
 * ```
 *
 * ### 4. Transform 百分比平移
 * ```tsx
 * // 一维平移
 * <div className="translate-x-10p">transform: translateX(10%)</div>
 * <div className="translate-y--20p">transform: translateY(-20%)</div>
 * <div className="-translate-x-10p">transform: translateX(-10%)</div>
 *
 * // 二维平移
 * <div className="translate-10-20p">transform: translate(10%, 20%)</div>
 * <div className="-translate-10-20p">transform: translate(-10%, -20%)</div>
 *
 * // 三维平移
 * <div className="translate-10-20-5p">transform: translate3d(10%, 20%, 5%)</div>
 * ```
 *
 * ### 5. 渐变背景
 * ```tsx
 * // 默认90度渐变
 * <div className="bg-gradient-primary-secondary">linear-gradient(90deg, primary, secondary)</div>
 * <div className="bg-gradient-#0372F1-#00F2FE">linear-gradient(90deg, #0372F1, #00F2FE)</div>
 *
 * // 指定角度
 * <div className="bg-gradient-180deg-primary-secondary">linear-gradient(180deg, primary, secondary)</div>
 * <div className="bg-gradient-45deg-#f00-#00f">linear-gradient(45deg, #f00, #00f)</div>
 * ```
 *
 * ## 已移除的规则（与 presetWind3 冲突或无效）
 *
 * 以下规则已被移除，由 presetWind3 处理：
 *
 * 1. `text-([a-zA-Z0-9-#]+)` - 宽泛正则，会拦截 presetWind3 的 text-xl、text-center 等
 *    - 使用 theme.colors + presetWind3 的 text-primary、text-red/50 替代
 *
 * 2. `bg-([a-zA-Z0-9-#]+)` - 同上，会拦截 bg-cover、bg-center 等
 *    - 使用 theme.colors + presetWind3 的 bg-primary、bg-red/50 替代
 *
 * 3. `border-([a-zA-Z0-9-#]+)` - 会拦截 border-1、border-solid 等
 *    - 使用 theme.colors + presetWind3 的 border-primary 替代
 *
 * 4. `first-child:xxx`、`last-child:xxx`、`nth-child-n:xxx` - 产出无效 CSS
 *    - 使用 presetWind3 的变体语法：`first:text-red`、`last:bg-blue` 替代
 *
 * 5. 底部静态颜色规则 - 与 theme.colors 完全冗余
 *    - 原代码 `...Object.entries(colors).map(...)` 生成的 text-primary、bg-primary 等
 *    - 由 presetWind3 通过 theme.colors 自动支持，还额外提供透明度修饰符能力
 */
export function createRules(colors: Record<string, string>): Rule<object>[] {
  return [
    // =============================================
    // 内联 HEX 颜色（presetWind3 不支持 text-#xxx 语法）
    // =============================================

    // 文本 HEX 颜色: text-#333, text-#FF5500, text-#00000080
    // 示例: <span className="text-#333">深灰文本</span>
    [
      /^text-(#[a-fA-F0-9]{3,8})$/,
      ([, color]) => {
        const parsedColor = parseColor(color, colors);
        return parsedColor ? { color: parsedColor } : undefined;
      },
    ],

    // 背景 HEX 颜色: bg-#f5f5f5, bg-#00099336
    // 示例: <div className="bg-#f5f5f5">浅灰背景</div>
    [
      /^bg-(#[a-fA-F0-9]{3,8})$/,
      ([, color]) => {
        const parsedColor = parseColor(color, colors);
        return parsedColor ? { 'background-color': parsedColor } : undefined;
      },
    ],

    // 边框 HEX 颜色: border-#f0f0f0
    // 示例: <div className="border border-#f0f0f0">浅灰边框</div>
    [
      /^border-(#[a-fA-F0-9]{3,8})$/,
      ([, color]) => {
        const parsedColor = parseColor(color, colors);
        return parsedColor ? { 'border-color': parsedColor } : undefined;
      },
    ],

    // 边框颜色（带 border-color- 前缀）: border-color-primary, border-color-#333
    // 示例: <div className="border-color-primary">主题色边框</div>
    [
      /^border-color-([a-zA-Z0-9-#]+)$/,
      ([, color]) => {
        const parsedColor = parseColor(color, colors);
        return parsedColor ? { 'border-color': parsedColor } : undefined;
      },
    ],

    // =============================================
    // 百分比单位规则（presetWind3 不支持 *p 后缀）
    // presetWind3 使用分数形式：w-1/2 = 50%, w-1/4 = 25%
    // 本规则提供更直观的百分比写法：w-50p = 50%, w-25p = 25%
    // =============================================

    // 宽度和高度百分比
    // 示例: w-50p → width: 50%, h-100p → height: 100%
    [/^w-(\d+)p$/, ([, p]) => ({ width: `${p}%` })],
    [/^h-(\d+)p$/, ([, p]) => ({ height: `${p}%` })],

    // 位置百分比
    // 示例: top-25p → top: 25%, left-50p → left: 50%
    [/^top-(\d+)p$/, ([, p]) => ({ top: `${p}%` })],
    [/^left-(\d+)p$/, ([, p]) => ({ left: `${p}%` })],
    [/^right-(\d+)p$/, ([, p]) => ({ right: `${p}%` })],
    [/^bottom-(\d+)p$/, ([, p]) => ({ bottom: `${p}%` })],

    // 最大/最小宽度和高度百分比
    // 示例: max-w-80p → max-width: 80%, min-h-50p → min-height: 50%
    [/^max-w-(\d+)p$/, ([, p]) => ({ 'max-width': `${p}%` })],
    [/^min-w-(\d+)p$/, ([, p]) => ({ 'min-width': `${p}%` })],
    [/^max-h-(\d+)p$/, ([, p]) => ({ 'max-height': `${p}%` })],
    [/^min-h-(\d+)p$/, ([, p]) => ({ 'min-height': `${p}%` })],

    // padding 百分比
    // 示例: p-5p → padding: 5%, pt-10p → padding-top: 10%, px-20p → padding-left/right: 20%
    [/^p-(\d+)p$/, ([, p]) => ({ padding: `${p}%` })],
    [/^pt-(\d+)p$/, ([, p]) => ({ 'padding-top': `${p}%` })],
    [/^pr-(\d+)p$/, ([, p]) => ({ 'padding-right': `${p}%` })],
    [/^pb-(\d+)p$/, ([, p]) => ({ 'padding-bottom': `${p}%` })],
    [/^pl-(\d+)p$/, ([, p]) => ({ 'padding-left': `${p}%` })],
    [/^px-(\d+)p$/, ([, p]) => ({ 'padding-left': `${p}%`, 'padding-right': `${p}%` })],
    [/^py-(\d+)p$/, ([, p]) => ({ 'padding-top': `${p}%`, 'padding-bottom': `${p}%` })],

    // margin 百分比
    // 示例: m-10p → margin: 10%, mt-5p → margin-top: 5%, mx-10p → margin-left/right: 10%
    [/^m-(\d+)p$/, ([, p]) => ({ margin: `${p}%` })],
    [/^mt-(\d+)p$/, ([, p]) => ({ 'margin-top': `${p}%` })],
    [/^mr-(\d+)p$/, ([, p]) => ({ 'margin-right': `${p}%` })],
    [/^mb-(\d+)p$/, ([, p]) => ({ 'margin-bottom': `${p}%` })],
    [/^ml-(\d+)p$/, ([, p]) => ({ 'margin-left': `${p}%` })],
    [/^mx-(\d+)p$/, ([, p]) => ({ 'margin-left': `${p}%`, 'margin-right': `${p}%` })],
    [/^my-(\d+)p$/, ([, p]) => ({ 'margin-top': `${p}%`, 'margin-bottom': `${p}%` })],

    // 行高百分比
    // 示例: leading-150p → line-height: 150%
    [/^leading-(\d+)p$/, ([, p]) => ({ 'line-height': `${p}%` })],

    // =============================================
    // 自定义阴影规则
    // presetWind3 支持 shadow-sm/shadow-lg 等预设值
    // 本规则提供更灵活的自定义参数
    // =============================================

    // 5参数: shadow-x-y-blur-spread-color
    // 示例: shadow-0-4-8-0-primary → 0 4px 8px 0 primary
    [
      /^shadow-(\d+)-(\d+)-(\d+)-(\d+)-(.+)$/,
      ([, x, y, blur, spread, color]) => {
        const parsedColor = parseColor(color, colors);
        // 颜色无效时返回 undefined，避免吞掉非阴影类名
        return parsedColor
          ? { 'box-shadow': `${x}px ${y}px ${blur}px ${spread}px ${parsedColor}` }
          : undefined;
      },
    ],

    // 4参数: shadow-x-y-blur-color
    // 示例: shadow-2-4-8-primary → 2px 4px 8px primary
    [
      /^shadow-(\d+)-(\d+)-(\d+)-(.+)$/,
      ([, x, y, blur, color]) => {
        const parsedColor = parseColor(color, colors);
        return parsedColor ? { 'box-shadow': `${x}px ${y}px ${blur}px ${parsedColor}` } : undefined;
      },
    ],

    // 2参数: shadow-size-color
    // 示例: shadow-10-primary → 0 5px 10px primary (y偏移 = size/2)
    [
      /^shadow-(\d+)-(.+)$/,
      ([, size, color]) => {
        const s = Number(size);
        const parsedColor = parseColor(color, colors);
        return parsedColor ? { 'box-shadow': `0 ${s / 2}px ${s}px ${parsedColor}` } : undefined;
      },
    ],

    // 1参数: shadow-size（使用默认阴影颜色）
    // 示例: shadow-10 → 0 5px 10px shadowColor
    [
      /^shadow-(\d+)$/,
      ([, size]) => {
        const s = Number(size);
        return { 'box-shadow': `0 ${s / 2}px ${s}px ${colors.shadowColor}` };
      },
    ],

    // =============================================
    // Transform 百分比规则
    // presetWind3 支持 translate-x-4 (rem单位)
    // 本规则提供百分比单位的平移
    // =============================================

    // 一维平移（X、Y、Z）
    // 示例: translate-x-10p → transform: translateX(10%)
    [/^translate-x-(-?\d+(\.\d+)?)p$/, ([, value]) => ({ transform: `translateX(${value}%)` })],
    [/^translate-y-(-?\d+(\.\d+)?)p$/, ([, value]) => ({ transform: `translateY(${value}%)` })],
    [/^translate-z-(-?\d+(\.\d+)?)p$/, ([, value]) => ({ transform: `translateZ(${value}%)` })],

    // 二维平移（X+Y）
    // 示例: translate-10-20p → transform: translate(10%, 20%)
    [
      /^translate-(-?\d+(\.\d+)?)-(-?\d+(\.\d+)?)p$/,
      ([, x, , y]) => ({ transform: `translate(${x}%, ${y}%)` }),
    ],

    // 三维平移（X+Y+Z）
    // 示例: translate-10-20-5p → transform: translate3d(10%, 20%, 5%)
    [
      /^translate-(-?\d+(\.\d+)?)-(-?\d+(\.\d+)?)-(-?\d+(\.\d+)?)p$/,
      ([, x, , y, , z]) => ({ transform: `translate3d(${x}%, ${y}%, ${z}%)` }),
    ],

    // 负方向快捷写法（X、Y、Z）- 只接受正数值
    // 示例: -translate-x-10p → transform: translateX(-10%)
    [/^-translate-x-(\d+(\.\d+)?)p$/, ([, value]) => ({ transform: `translateX(-${value}%)` })],
    [/^-translate-y-(\d+(\.\d+)?)p$/, ([, value]) => ({ transform: `translateY(-${value}%)` })],
    [/^-translate-z-(\d+(\.\d+)?)p$/, ([, value]) => ({ transform: `translateZ(-${value}%)` })],

    // 负方向二维平移 - 只接受正数值
    // 示例: -translate-10-20p → transform: translate(-10%, -20%)
    [
      /^-translate-(\d+(\.\d+)?)-(\d+(\.\d+)?)p$/,
      ([, x, , y]) => ({ transform: `translate(-${x}%, -${y}%)` }),
    ],

    // 负方向三维平移 - 只接受正数值
    // 示例: -translate-10-20-5p → transform: translate3d(-10%, -20%, -5%)
    [
      /^-translate-(\d+(\.\d+)?)-(\d+(\.\d+)?)-(\d+(\.\d+)?)p$/,
      ([, x, , y, , z]) => ({ transform: `translate3d(-${x}%, -${y}%, -${z}%)` }),
    ],

    // =============================================
    // 渐变背景规则
    // presetWind3 支持 bg-gradient-to-r/l/t/b 方向渐变
    // 本规则提供两个颜色之间的直接渐变
    // =============================================

    // 带角度: bg-gradient-90deg-primary-secondary
    // 示例: bg-gradient-180deg-primary-secondary → linear-gradient(180deg, primary, secondary)
    [
      /^bg-gradient-(\d+)deg-(.+)-(.+)$/,
      ([, angle, color1, color2]) => {
        const parsedColor1 = parseColor(color1, colors);
        const parsedColor2 = parseColor(color2, colors);
        return parsedColor1 && parsedColor2
          ? { background: `linear-gradient(${angle}deg, ${parsedColor1}, ${parsedColor2})` }
          : undefined;
      },
    ],

    // 默认90度: bg-gradient-primary-secondary, bg-gradient-#0372F1-#00F2FE
    // 使用 (?!to-) 避免拦截 presetWind3 的 bg-gradient-to-r/l/t/b 等
    // 示例: bg-gradient-primary-secondary → linear-gradient(90deg, primary, secondary)
    [
      /^bg-gradient-(?!to-)(.+)-(.+)$/,
      ([, color1, color2]) => {
        const parsedColor1 = parseColor(color1, colors);
        const parsedColor2 = parseColor(color2, colors);
        return parsedColor1 && parsedColor2
          ? { background: `linear-gradient(90deg, ${parsedColor1}, ${parsedColor2})` }
          : undefined;
      },
    ],
  ] as Rule<object>[];
}
