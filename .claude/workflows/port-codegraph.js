export const meta = {
  name: 'port-codegraph',
  description: '把 stock-admin-web 的 codegraph 模块(4 子页)移植进 codev web-ui, 适配 graphapi + API.* 类型, 测试+审计',
  phases: [
    { title: 'Phase1-common', detail: '建 common 适配层(services/types/utils/style)' },
    { title: 'Phase2-subpages', detail: '4 子页并行移植: filelist/files/graph/table (crosslink 子页因 cross-link 退役去掉)' },
    { title: 'Phase3-wiring', detail: 'routes.ts + 菜单接线' },
    { title: 'Phase4-audit', detail: '审计 diff: 规约符合度 + API 契约正确性' },
  ],
};

const SRC = 'D:\\WorkSpace\\platform\\apps\\stock-admin-web\\src\\pages\\codegraph';
const DST = 'D:\\WorkSpace\\codev-platform\\web-ui\\src\\pages\\codegraph';
const GRAPHAPI = 'D:\\WorkSpace\\codev-platform\\web-ui\\src\\services\\apis\\graphapi.ts';
const TYPINGS = 'D:\\WorkSpace\\codev-platform\\web-ui\\src\\services\\apis\\typings.d.ts';

// web-ui 前端铁律(MCP search_docs 也可查 module 规则, 但这里给关键点防漏)
const RULES = [
  '禁用 useRequest, 一律 useState+useCallback+useEffect 三件套',
  '禁止 antd 原生 Drawer/Modal/Table, 用 @/components/Drawer|Modal|ResizableTable',
  'antd6: Card classNames.root(布局类加 i: 前缀) / Statistic styles.content / Alert title / destroyOnHidden / Modal.confirm 换 showConfirm',
  'UnoCSS baseFontSize=4(类名数字=px), 颜色用 bg-#xxx 不用 [#xxx]; 动态色用 style 注入',
  '禁止 any; 类型从 @/pages/codegraph/common/types 取; typings.d.ts 是全局 namespace 禁 import',
  '组件文件名 PascalCase 且与 default export 同名; index/utils/types 小写',
  'JSX 禁内联函数(react/jsx-no-bind), 传组件的回调用 useCallback',
  '业务时间显示用 dayjs(v).format("YYYY/MM/DD HH:mm:ss")',
  '完成前对自己产出的文件跑 eslint --fix(cwd web-ui)',
];

const REPORT_SCHEMA = {
  type: 'object',
  required: ['files', 'notes'],
  properties: {
    files: { type: 'array', items: { type: 'string' }, description: '本次创建/修改的文件绝对路径' },
    notes: { type: 'string', description: '关键适配决策 + 字段映射差异 + 风险, 200 字内' },
    issues: { type: 'array', items: { type: 'string' }, description: '未解决问题/需主控关注的点' },
  },
};

// ---------- Phase 1: common 适配层 ----------
phase('Phase1-common');
const phase1 = await agent(
  [
    '【目标】 建 codev web-ui 的 codegraph 公共适配层, 供 5 个子页复用',
    `【源参考】 ${SRC}\\common\\ (types.ts/services.ts/utils.ts/style.less) —— 这是 stock 版, 照搬结构但换数据源`,
    `【产出目录】 ${DST}\\common\\`,
    '【适配规则】',
    `- services.ts: 把 stock 对 '@/services/apis/codegraph/codegraph' 的调用, 改为 import 自 '@/services/apis/graphapi'(同名 postStats/postSearch/postCodegraphNode/postNeighbors/postFileTree/postGraph 已存在); 同样提供 fetchStats/fetchSearch/fetchNode/fetchNeighbors/fetchFileTree/fetchGraph, 剥 res.data`,
    `- types.ts: stock 用 CG.* 命名空间; codev 改用 API.* —— 先读 ${TYPINGS} 找 API.Codegraph* 的真实字段, 把 NodeDTO/EdgeDTO/StatsResponse/SearchResponse/NeighborsResponse/FileTreeResponse/GraphResponse 等别名指向对应 API.* 类型。NodeKind/EdgeKind/Language 等字面量联合类型直接照搬。若 API.* 字段名与 stock CG.* 不一致, 在 notes 里逐条列出差异`,
    `- utils.ts / style.less: 照搬 stock 版, 仅按需调整 import 路径`,
    `【必读】 ${GRAPHAPI} (确认可用函数与返回类型) + ${TYPINGS} (确认 API.Codegraph* 字段)`,
    '【禁止】 不动 graphapi.ts/typings.d.ts(生成物); 不用 any; 不 import typings.d.ts',
    '【规约】 ' + RULES.join(' / '),
    '【输出】 files + notes(重点: API.* vs CG.* 字段差异清单, 子页要据此改字段访问) + issues',
  ].join('\n'),
  { schema: REPORT_SCHEMA, phase: 'Phase1-common', label: 'common' },
);

// ---------- Phase 2: 5 子页并行 ----------
phase('Phase2-subpages');
const SUBPAGES = ['filelist', 'files', 'graph', 'table'];
const phase2 = await parallel(
  SUBPAGES.map((sub) => () =>
    agent(
      [
        `【目标】 移植 codegraph 子页 "${sub}" 进 codev web-ui`,
        `【源】 ${SRC}\\${sub}\\ (全部 .tsx/.ts/.less, 含 components/)`,
        `【产出目录】 ${DST}\\${sub}\\ (保持子目录结构)`,
        `【依赖】 公共层已在 ${DST}\\common\\ 建好 —— 先读 common/services.ts + common/types.ts, 子页一律从这里取数据函数(fetchXxx)和类型, 不要再 import '@/services/apis/codegraph/*'`,
        '【适配步骤】',
        `1. 读源子页所有文件 + ${DST}\\common\\ 的 services/types`,
        '2. 数据调用换成 common/services 的 fetchXxx; 类型换成 common/types 的别名',
        '3. 按 common Phase1 报告的字段差异(若有)修正 record.xxx 字段访问',
        '4. 按 web-ui 规约改造: useRequest→三件套; antd Drawer/Modal/Table→@/components 封装; antd6 废弃 props; UnoCSS 单位; 颜色 style 注入',
        '5. 对产出文件跑 eslint --fix(cwd: D:\\WorkSpace\\codev-platform\\web-ui)',
        '【禁止】 不碰 common/ 与别的子页目录; 不动 graphapi/typings; 不用 any; 不留 useRequest',
        '【规约】 ' + RULES.join(' / '),
        '【输出】 files(本子页绝对路径) + notes(150 字内) + issues',
      ].join('\n'),
      { schema: REPORT_SCHEMA, phase: 'Phase2-subpages', label: sub },
    ),
  ),
);

// ---------- Phase 3: 路由 + 菜单接线 ----------
phase('Phase3-wiring');
const phase3 = await agent(
  [
    '【目标】 把新 codegraph 5 子页接进路由 + 顶层菜单',
    '【文件】 D:\\WorkSpace\\codev-platform\\web-ui\\config\\routes.ts (路由) + D:\\WorkSpace\\codev-platform\\web-ui\\src\\menus\\ (菜单定义, 先 grep MENU_ITEMS 定位)',
    `【接什么】 ${DST}\\ 下的 4 子页: filelist/files/graph/table, 各自的 index.tsx`,
    '【做法】 先读现有 routes.ts 与 menus 的写法照葫芦画瓢; 建一个 "代码图谱/CodeGraph" 父菜单, 4 子页作子路由(path 形如 /codegraph/table 等); 标题中文',
    '【禁止】 不改其它路由; 不动既有 pages/graph; 路径全小写无符号',
    '【输出】 files + notes(新增的 path 清单) + issues',
  ].join('\n'),
  { schema: REPORT_SCHEMA, phase: 'Phase3-wiring', label: 'routes+menu' },
);

// ---------- Phase 4: 审计 ----------
phase('Phase4-audit');
const allFiles = [phase1, ...phase2, phase3]
  .filter(Boolean)
  .flatMap((r) => r.files || []);
const phase4 = await agent(
  [
    '【目标】 审计本次 codegraph 移植的全部产出, 找规约违规 + API 契约错误, 不改代码只报告',
    '【审计文件】\n' + allFiles.join('\n'),
    '【审计维度】',
    `1. API 契约: fetchXxx 调用的 graphapi 函数/入参/返回字段是否与 ${TYPINGS} 的 API.Codegraph* 一致(字段名打错会运行时 undefined)`,
    '2. 规约: 有无残留 useRequest / antd 原生 Drawer|Modal|Table / antd4 废弃 props(valueStyle/message=/destroyOnClose/Modal.confirm) / any / import typings.d.ts / 内联函数',
    '3. 命名: 组件文件 PascalCase 与 default export 同名; index/utils/types 小写',
    '4. 残留 stock 痕迹: 还在 import "@/services/apis/codegraph" 或用 CG.* 命名空间',
    '【输出】 files=[] ; notes=总体结论(通过/有问题) ; issues=逐条列违规(文件:行 + 问题 + 修法), 无则空数组',
  ].join('\n'),
  { schema: REPORT_SCHEMA, phase: 'Phase4-audit', label: 'audit' },
);

return {
  common: phase1,
  subpages: phase2.filter(Boolean),
  wiring: phase3,
  audit: phase4,
};
