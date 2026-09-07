import { readFileSync } from 'fs';
import { resolve } from 'path';

// 默认颜色定义
// 这些颜色传入 theme.colors 后，presetWind3 自动生成 text-*/bg-*/border-* 等工具类
// 不要使用 text-/bg- 前缀的 key，否则产生 text-text-primary 双前缀
export const defaultColors: Record<string, string> = {
  primary: 'rgb(255, 197, 0)',
  secondary: 'rgb(58, 58, 60)',
  orange: 'rgb(255, 149, 0)',
  red: 'rgb(255, 59, 48)',
  green: 'rgb(52, 199, 89)',
  blue: 'rgb(12, 122, 255)',
  shadowColor: 'rgba(0, 0, 0, 0.2)',
};

/**
 * 读取并解析 Less 变量（同步版本）
 */
export function extractLessVariablesSync(): Record<string, string> {
  try {
    const lessFilePath = resolve(__dirname, '../../../src/styles/variables.less');
    const lessContent = readFileSync(lessFilePath, 'utf-8');

    const variables: Record<string, string> = {};
    const variableRegex = /@([a-zA-Z0-9_-]+)\s*:\s*([^;]+);/g;
    let match;

    while ((match = variableRegex.exec(lessContent)) !== null) {
      const varName = match[1];
      let varValue = match[2].trim();

      const refRegex = /@([a-zA-Z0-9_-]+)/g;
      varValue = varValue.replace(refRegex, (_, refName) => {
        return variables[refName] || `@${refName}`;
      });

      variables[varName] = varValue;
    }

    return variables;
  } catch (error) {
    console.warn('Failed to extract Less variables:', error);
    return {};
  }
}

/**
 * 颜色解析函数 - 支持多种格式
 * 支持：预定义颜色、HEX、RGB/RGBA、HSL/HSLA、二进制格式
 */
export function parseColor(color: string, colorMap: Record<string, string>): string | null {
  if (color in colorMap) return colorMap[color];

  const hex3 = /^#([0-9A-F]{3})$/i;
  const hex4 = /^#([0-9A-F]{4})$/i;
  const hex6 = /^#([0-9A-F]{6})$/i;
  const hex8 = /^#([0-9A-F]{8})$/i;

  if (hex3.test(color)) {
    const [, rgb] = color.match(hex3)!;
    return `#${rgb[0]}${rgb[0]}${rgb[1]}${rgb[1]}${rgb[2]}${rgb[2]}`;
  }
  if (hex4.test(color)) {
    const [, rgba] = color.match(hex4)!;
    return `#${rgba[0]}${rgba[0]}${rgba[1]}${rgba[1]}${rgba[2]}${rgba[2]}${rgba[3]}${rgba[3]}`;
  }
  if (hex6.test(color)) return color;
  if (hex8.test(color)) {
    const [, hex] = color.match(hex8)!;
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    const a = Math.round((parseInt(hex.substring(6, 8), 16) / 255) * 100) / 100;
    return `rgba(${r}, ${g}, ${b}, ${a})`;
  }

  const rgb = /^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/i;
  const rgba = /^rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([\d.]+)\s*\)$/i;

  if (rgb.test(color)) {
    const [, r, g, b] = color.match(rgb)!;
    if (+r > 255 || +g > 255 || +b > 255) return null;
    return `rgb(${r}, ${g}, ${b})`;
  }
  if (rgba.test(color)) {
    const [, r, g, b, a] = color.match(rgba)!;
    if (+r > 255 || +g > 255 || +b > 255 || +a > 1) return null;
    return `rgba(${r}, ${g}, ${b}, ${a})`;
  }

  const hsl = /^hsl\(\s*(\d{1,3})\s*,\s*(\d{1,3})%\s*,\s*(\d{1,3})%\s*\)$/i;
  const hsla = /^hsla\(\s*(\d{1,3})\s*,\s*(\d{1,3})%\s*,\s*(\d{1,3})%\s*,\s*([\d.]+)\s*\)$/i;

  if (hsl.test(color)) {
    const [, h, s, l] = color.match(hsl)!;
    if (+h > 360 || +s > 100 || +l > 100) return null;
    return `hsl(${h}, ${s}%, ${l}%)`;
  }
  if (hsla.test(color)) {
    const [, h, s, l, a] = color.match(hsla)!;
    if (+h > 360 || +s > 100 || +l > 100 || +a > 1) return null;
    return `hsla(${h}, ${s}%, ${l}%, ${a})`;
  }

  const binary = /^b:(\d{8})_(\d{8})_(\d{8})$/;
  if (binary.test(color)) {
    const [, r, g, b] = color.match(binary)!;
    return `rgb(${parseInt(r, 2)}, ${parseInt(g, 2)}, ${parseInt(b, 2)})`;
  }

  return null;
}

function isColorValue(value: string): boolean {
  const trimmed = value.trim();
  return /^(#|rgb|rgba|hsl|hsla)/i.test(trimmed);
}

/**
 * 创建颜色配置对象
 * 合并默认颜色和从 Less 提取的颜色，自动过滤非颜色变量
 */
export function createColorConfig(): Record<string, string> {
  const lessVariables = extractLessVariablesSync();
  const themeColors: Record<string, string> = {};

  Object.entries(lessVariables).forEach(([key, value]) => {
    if (!isColorValue(value)) return;
    themeColors[key] = value;
    if (key.match(/[A-Z]/)) {
      const kebabKey = key.replace(/([A-Z])/g, '-$1').toLowerCase();
      themeColors[kebabKey] = value;
    }
  });

  return { ...defaultColors, ...themeColors };
}
