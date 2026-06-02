// config/utils/unocss/extractors.ts
// UnoCSS 自定义提取器

/**
 * Antd 6 classNames API 提取器
 * 用于提取 classNames={{ key: 'value' }} 中的类名
 */
export const antdClassNamesExtractor = {
  name: 'antd-classnames-extractor',
  order: 0,
  extract({ code }: { code: string }) {
    const classNames = new Set<string>();

    // 匹配 classNames={{ ... }} 模式（单行和多行统一处理）
    // 使用 [\s\S] 替代 . 来匹配包括换行符在内的所有字符
    const classNamesMultilineRegex = /classNames\s*=\s*\{\{([\s\S]*?)\}\}/g;
    let match;
    while ((match = classNamesMultilineRegex.exec(code)) !== null) {
      const content = match[1];
      const valueRegex = /['"`]([^'"`]+)['"`]/g;
      let valueMatch;

      while ((valueMatch = valueRegex.exec(content)) !== null) {
        const value = valueMatch[1];
        value.split(/\s+/).forEach((className) => {
          if (className) {
            classNames.add(className);
          }
        });
      }
    }

    return Array.from(classNames);
  },
};

/**
 * 所有自定义提取器
 */
export const extractors = [antdClassNamesExtractor];
