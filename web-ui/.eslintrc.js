module.exports = {
  extends: [require.resolve('@umijs/lint/dist/config/eslint')],
  globals: {
    page: true,
    REACT_APP_ENV: true,
  },
  plugins: ['filenames', 'local-rules', 'react'],
  rules: {
    indent: ['error', 2],
    'space-before-function-paren': 'off',
    // 函数行数限制（自定义规则：跳过 return 中的 JSX/对象，只限制方法逻辑）
    'max-lines-per-function': 'off',
    'local-rules/max-function-lines': [
      'error',
      {
        max: 100, // 普通函数逻辑最大 100 行
        maxExportDefault: 900, // export default 函数最大 900 行
        skipBlankLines: true,
        skipComments: true,
        skipJsx: true, // 跳过 return 中的 JSX/对象/数组
      },
    ],
    '@typescript-eslint/no-unused-vars': 'error',
    'prefer-const': 'error', // 强制：不变的变量必须用 const，禁止多余的 let
    'block-scoped-var': 'error', // 禁止在块级作用域外访问变量（防止 var 乱象）
    'no-var': 'error', // 禁止使用 var（绝对禁止）
    'func-names': ['error', 'as-needed'],

    // 文件行数限制（兜底规则，防止极端情况）
    'max-lines': [
      'warn',
      {
        max: 1200, // 文件最大 1200 行（兜底）
        skipBlankLines: true,
        skipComments: true,
      },
    ],
    // 复杂度控制
    'max-depth': ['error', 4], // 最大嵌套深度 4 层

    // 禁止在 JSX props 中直接调用/定义函数（应提取为具名函数或 useCallback）
    'react/jsx-no-bind': [
      'error',
      {
        allowArrowFunctions: false, // 禁止箭头函数: onClick={() => fn()}
        allowBind: false, // 禁止 .bind(): onClick={fn.bind(this)}
        allowFunctions: false, // 禁止匿名函数: onClick={function() {}}
        ignoreDOMComponents: true, // 允许原生 DOM 标签（div/button 等）使用
        ignoreRefs: true, // 允许 ref 回调
      },
    ],
  },
  ignorePatterns: [
    '!.eslintrc.js',
    'node_modules',
    'dist',
    '**/*.less',
    '**/*.md',
    '**/locales/**',
  ],
  overrides: [
    // src 目录基础规则（排除 components 目录和特殊文件）
    {
      files: ['./src/**/*.{js,jsx,ts,tsx}'],
      excludedFiles: [
        '**/components/**/*.{js,jsx,ts,tsx}',
        '**/typings.d.ts',
        '**/typings.d.tsx',
        '**/index.ts',
        '**/index.tsx',
        '**/types.ts',
        '**/types.tsx',
        '**/utils.ts',
        '**/utils.tsx',
        '**/const.ts',
        '**/const.tsx',
        '**/*.d.ts',
        '**/*.style.ts',
        '**/Admin.tsx',
        '**/404.tsx',
        '**/requestErrorConfig.ts',
      ],
      rules: {
        'filenames/match-regex': ['error', '^[a-z][a-z0-9]*$'],
      },
    },
    // components 目录规则（包括 src/components 和 src/pages/components）
    {
      files: [
        './src/**/components/**/*.{js,jsx,ts,tsx}',
        './src/pages/**/components/**/*.{js,jsx,ts,tsx}',
      ],
      excludedFiles: [
        '**/typings.d.ts',
        '**/typings.d.tsx',
        '**/index.ts',
        '**/index.tsx',
        '**/types.ts',
        '**/types.tsx',
        '**/utils.ts',
        '**/utils.tsx',
        '**/const.ts',
        '**/const.tsx',
      ],
      rules: {
        'filenames/match-regex': ['error', '^[A-Z][a-zA-Z0-9]*$'],
        'filenames/match-exported': 'error',
      },
    },
    // Columns.tsx 文件取消函数行数限制，且 render 函数中 record 是闭包参数，允许箭头函数
    {
      files: ['**/*Columns.tsx', '**/Columns.tsx'],
      rules: {
        'local-rules/max-function-lines': 'off',
        'react/jsx-no-bind': 'off',
      },
    },
    // 特殊文件规则
    {
      files: [
        '**/index.{ts,tsx}',
        '**/utils.{ts,tsx}',
        '**/types.{ts,tsx}',
        '**/const.{ts,tsx}',
        '**/typings.d.{ts,tsx}',
      ],
      rules: {
        'filenames/match-regex': ['error', '^[a-z][a-z0-9.]*$'],
      },
    },
  ],
};
