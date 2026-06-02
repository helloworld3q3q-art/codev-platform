/**
 * 自定义 ESLint 规则：函数行数限制
 *
 * 规则说明：
 * - export default + 有 return JSX：最大 900 行（React 组件主函数）
 * - 其他所有函数：最大 100 行（跳过 return 中的 JSX 行数计算）
 *
 * 支持的 export default 模式：
 * 1. export default function() {}
 * 2. const fn = () => {}; export default fn;
 * 3. const Component = forwardRef(() => {}); export default Component;
 * 4. function Name() {}; export default Name;
 */

module.exports = {
  'max-function-lines': {
    meta: {
      type: 'suggestion',
      docs: {
        description: 'Enforce a maximum number of lines per function, skipping return statements',
        category: 'Stylistic Issues',
        recommended: false,
      },
      schema: [
        {
          type: 'object',
          properties: {
            max: {
              type: 'integer',
              minimum: 1,
            },
            maxExportDefault: {
              type: 'integer',
              minimum: 1,
            },
            skipBlankLines: {
              type: 'boolean',
            },
            skipComments: {
              type: 'boolean',
            },
            skipJsx: {
              type: 'boolean',
            },
          },
          additionalProperties: false,
        },
      ],
    },
    create(context) {
      const option = context.options[0] || {};
      const maxLines = option.max || 100; // 普通函数最大 100 行
      const maxExportDefaultLines = option.maxExportDefault || 900; // export default 函数最大 900 行
      const skipBlankLines = option.skipBlankLines !== false;
      const skipComments = option.skipComments !== false;
      const skipJsx = option.skipJsx !== false; // 默认跳过 return 中的 JSX

      const sourceCode = context.getSourceCode();

      // 存储被 export default 的变量名
      const exportedDefaultNames = new Set();

      // 存储需要检查的函数
      const functionsToCheck = [];

      /**
       * 收集 export default 的变量名
       */
      function collectExportDefaultNames(node) {
        const declaration = node.declaration;
        // export default Identifier
        if (declaration.type === 'Identifier') {
          exportedDefaultNames.add(declaration.name);
        }
        // export default function name() {}
        if (declaration.type === 'FunctionDeclaration' && declaration.id) {
          exportedDefaultNames.add(declaration.id.name);
        }
      }

      /**
       * 收集函数节点
       */
      function collectFunction(node) {
        functionsToCheck.push(node);
      }

      /**
       * 检查节点是否被 export default 导出
       */
      function isExportDefault(node) {
        // 情况 1: export default function() {} (直接导出函数声明)
        if (node.parent && node.parent.type === 'ExportDefaultDeclaration') {
          return true;
        }

        // 情况 2: function Name() {}; export default Name;
        if (
          node.type === 'FunctionDeclaration' &&
          node.id &&
          exportedDefaultNames.has(node.id.name)
        ) {
          return true;
        }

        // 向上查找，遇到函数边界停止
        let checkNode = node;
        while (checkNode) {
          if (checkNode !== node) {
            const isFunctionBoundary =
              checkNode.type === 'FunctionDeclaration' ||
              checkNode.type === 'FunctionExpression' ||
              checkNode.type === 'ArrowFunctionExpression';
            if (isFunctionBoundary) {
              return false;
            }
          }

          // 情况 3: const fn = () => {}; export default fn;
          if (
            checkNode.type === 'VariableDeclarator' &&
            checkNode.id &&
            checkNode.id.type === 'Identifier'
          ) {
            if (exportedDefaultNames.has(checkNode.id.name)) {
              return true;
            }
          }

          // 情况 4: forwardRef(() => {}) 或 React.memo(() => {})
          if (checkNode.parent) {
            const parent = checkNode.parent;
            if (parent.type === 'CallExpression') {
              const callee = parent.callee;
              const isHOC =
                (callee.type === 'Identifier' &&
                  (callee.name === 'forwardRef' || callee.name === 'memo')) ||
                (callee.type === 'MemberExpression' &&
                  callee.object.type === 'Identifier' &&
                  callee.object.name === 'React' &&
                  callee.property.type === 'Identifier' &&
                  (callee.property.name === 'forwardRef' || callee.property.name === 'memo'));

              if (isHOC) {
                let nextNode = parent.parent;
                while (nextNode) {
                  if (
                    nextNode.type === 'FunctionDeclaration' ||
                    nextNode.type === 'FunctionExpression' ||
                    nextNode.type === 'ArrowFunctionExpression'
                  ) {
                    return false;
                  }
                  if (
                    nextNode.type === 'VariableDeclarator' &&
                    nextNode.id &&
                    nextNode.id.type === 'Identifier'
                  ) {
                    if (exportedDefaultNames.has(nextNode.id.name)) {
                      return true;
                    }
                    break;
                  }
                  nextNode = nextNode.parent;
                }
              }
            }
          }

          if (checkNode.parent && checkNode.parent.type === 'Program') {
            if (checkNode.type === 'VariableDeclaration') {
              for (const decl of checkNode.declarations) {
                if (
                  decl.id &&
                  decl.id.type === 'Identifier' &&
                  exportedDefaultNames.has(decl.id.name)
                ) {
                  return true;
                }
              }
            }
          }

          checkNode = checkNode.parent;
        }

        return false;
      }

      /**
       * 查找函数中所有 return 语句的参数节点
       */
      function findReturnArgumentLines(node) {
        const skipLines = new Set();
        const visited = new WeakSet();

        function traverse(currentNode) {
          if (!currentNode || typeof currentNode !== 'object') return;
          if (visited.has(currentNode)) return;
          visited.add(currentNode);

          // 检查 ReturnStatement
          if (currentNode.type === 'ReturnStatement' && currentNode.argument) {
            const argument = currentNode.argument;
            // 只跳过 JSXElement、JSXFragment（React Node / HTML 元素）
            if (argument.type === 'JSXElement' || argument.type === 'JSXFragment') {
              const startLine = argument.loc.start.line;
              const endLine = argument.loc.end.line;
              for (let i = startLine; i <= endLine; i++) {
                skipLines.add(i);
              }
            }
          }

          // 递归遍历子节点（排除 parent 属性避免循环引用）
          for (const key of Object.keys(currentNode)) {
            if (key === 'parent' || key === 'loc' || key === 'range') continue;
            const child = currentNode[key];
            if (child && typeof child === 'object') {
              if (Array.isArray(child)) {
                child.forEach((c) => {
                  if (c && typeof c === 'object' && c.type) {
                    traverse(c);
                  }
                });
              } else if (child.type) {
                traverse(child);
              }
            }
          }
        }

        traverse(node);
        return skipLines;
      }

      /**
       * 计算函数行数
       */
      function getFunctionLines(node) {
        const startLine = node.loc.start.line;
        const endLine = node.loc.end.line;

        // 获取需要跳过的 return 参数行
        const skipJsxLines = skipJsx ? findReturnArgumentLines(node) : new Set();

        let lineCount = 0;

        for (let i = startLine; i <= endLine; i++) {
          // 跳过 return 中的 JSX 内容
          if (skipJsxLines.has(i)) {
            continue;
          }

          const line = sourceCode.lines[i - 1];
          const trimmedLine = line.trim();

          // 跳过空行
          if (skipBlankLines && trimmedLine === '') {
            continue;
          }

          // 跳过注释
          if (
            skipComments &&
            (trimmedLine.startsWith('//') ||
              trimmedLine.startsWith('/*') ||
              trimmedLine.startsWith('*'))
          ) {
            continue;
          }

          lineCount++;
        }

        return lineCount;
      }

      /**
       * 检查函数是否有 return JSX
       */
      function hasReturnJsx(node) {
        const jsxLines = findReturnArgumentLines(node);
        return jsxLines.size > 0;
      }

      /**
       * 在 Program:exit 时统一检查所有函数
       */
      function checkAllFunctions() {
        for (const node of functionsToCheck) {
          const isDefaultExport = isExportDefault(node);
          const hasJsx = hasReturnJsx(node);
          const lines = getFunctionLines(node);

          // 只有 export default 且有 return JSX 才给 900 行限制
          const limit = isDefaultExport && hasJsx ? maxExportDefaultLines : maxLines;

          if (lines > limit) {
            const functionName = node.id ? node.id.name : 'Anonymous function';
            const functionType =
              isDefaultExport && hasJsx ? 'Export default function with JSX' : 'Function';

            context.report({
              node,
              message:
                '{{type}} {{name}} has too many lines ({{lines}}). Maximum allowed is {{max}}.',
              data: {
                type: functionType,
                name: functionName,
                lines,
                max: limit,
              },
            });
          }
        }
      }

      return {
        ExportDefaultDeclaration: collectExportDefaultNames,
        FunctionDeclaration: collectFunction,
        FunctionExpression: collectFunction,
        ArrowFunctionExpression: collectFunction,
        'Program:exit': checkAllFunctions,
      };
    },
  },
};
