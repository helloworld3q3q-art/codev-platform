import presetRemToPx from '@unocss/preset-rem-to-px';
import { defineConfig, presetAttributify, presetWind3 } from 'unocss';
import { extractors } from './config/utils/unocss/extractors';
import { createRules } from './config/utils/unocss/rules';
import { shortcuts } from './config/utils/unocss/shortcuts';
import { createColorConfig } from './config/utils/unocss/utils';

export function createConfig({ strict = true, dev = true } = {}) {
  // 创建颜色配置对象（包含默认颜色和从 Less 提取的颜色）
  const colors = createColorConfig();

  // 创建规则集合
  const rules = createRules(colors);

  return defineConfig({
    envMode: dev ? 'dev' : 'build',
    presets: [
      presetAttributify({ strict }),
      presetWind3(),
      presetRemToPx({
        baseFontSize: 4,
      }),
    ],
    theme: {
      colors,
    },
    rules,
    shortcuts,
    extractors,
    variants: [
      // i: 权重提升变体 (双倍权重)
      (matcher) => {
        if (!matcher.startsWith('i:')) return matcher;
        return {
          matcher: matcher.slice(2),
          selector: (s) => `${s}${s}`,
        };
      },
      // h: 权重提升变体 (三倍权重)
      (matcher) => {
        if (!matcher.startsWith('h:')) return matcher;
        return {
          matcher: matcher.slice(2),
          selector: (s) => `${s}${s}${s}`,
        };
      },
      // placeholder 变体
      (matcher) => {
        if (!matcher.startsWith('placeholder:')) return matcher;
        return {
          matcher: matcher.slice(12),
          selector: (s) =>
            `${s}::placeholder, ${s}::-webkit-input-placeholder, ${s}::-moz-placeholder, ${s}:-ms-input-placeholder`,
        };
      },
      // 自定义 CSS 选择器变体
      (matcher) => {
        const variantSelector = /^\[([^\]]+)\]:/;
        const match = matcher.match(variantSelector);
        if (!match) return matcher;

        const [, selector] = match;
        return {
          matcher: matcher.replace(variantSelector, ''),
          selector: (s) => {
            if (selector.includes('&')) {
              return selector.replace(/&/g, s);
            }
            return `${s}${selector}`;
          },
        };
      },
    ],
  });
}

export default createConfig();
