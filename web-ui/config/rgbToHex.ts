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

export default rgbToHex;
