import themeVars from '@/styles/theme';
import { useEffect, useState } from 'react';

// 将 rgb 颜色转换为十六进制格式
const rgbToHex = (rgb: string) => {
  // 处理 rgb 和 rgba 格式
  const rgbRegex = /^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$/;
  const rgbaRegex = /^rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)$/;

  let r, g, b;

  if (rgbRegex.test(rgb)) {
    const matches = rgb.match(rgbRegex);
    if (matches) {
      r = parseInt(matches[1], 10);
      g = parseInt(matches[2], 10);
      b = parseInt(matches[3], 10);

      return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
    }
  } else if (rgbaRegex.test(rgb)) {
    const matches = rgb.match(rgbaRegex);
    if (matches) {
      r = parseInt(matches[1], 10);
      g = parseInt(matches[2], 10);
      b = parseInt(matches[3], 10);

      return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
    }
  }

  return rgb; // 如果不是 rgb/rgba 格式，返回原值
};

// 更新CSS变量
const updateCSSVariables = (colorPrimary: string) => {
  // 将十六进制转回rgb格式用于CSS变量
  const hexToRgb = (hex: string) => {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result
      ? `rgb(${parseInt(result[1], 16)}, ${parseInt(result[2], 16)}, ${parseInt(result[3], 16)})`
      : hex;
  };

  // 如果是十六进制格式，转回rgb
  const primaryRgb = colorPrimary.startsWith('#') ? hexToRgb(colorPrimary) : colorPrimary;

  // 更新CSS变量
  document.documentElement.style.setProperty('--primary', primaryRgb);
};

export default () => {
  // 初始化主题颜色
  const [colorPrimary, setColorPrimary] = useState(rgbToHex(themeVars.primary));

  // 更新主题
  const updateTheme = (newColorPrimary: string) => {
    setColorPrimary(newColorPrimary);
    updateCSSVariables(newColorPrimary);
  };

  // 初始化时设置CSS变量
  useEffect(() => {
    updateCSSVariables(colorPrimary);
  }, []);

  return {
    colorPrimary,
    updateTheme,
  };
};
