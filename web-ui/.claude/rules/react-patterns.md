# React 开发模式规范

---

## 🚨 禁用 `useRequest`（HIGHEST PRIORITY，2026-05-21 起）

**全局禁用** `@umijs/max` 导出的 `useRequest`。原因：与项目 fetch.ts 自定义封装（responseHandle / wrapRequest / 1s 去重）配合时数据流不可控，曾出现 service 正常返回但 `data` 始终 `undefined` 的 bug（ahooks v3 `formatResult` 配置项已移除 + service 引用变化时丢弃结果）。

**统一改用**：`useState + useCallback + useEffect` 三件套。

```tsx
// ❌ 禁止
import { useRequest } from '@umijs/max';
const { data, loading, refresh } = useRequest(postXxx, { formatResult: (res) => res.data });

// ✅ 标准模板（对象场景，初值 undefined）
const [data, setData] = useState<API.XxxResponse | undefined>(undefined);
const [loading, setLoading] = useState(false);

const loadData = useCallback(
  async (): Promise<void> => {
    setLoading(true);
    try {
      const res = await postXxx({});
      setData(res.data);
    } catch {
      setData(undefined);
    } finally {
      setLoading(false);
    }
  },
  [
    /* refreshDeps 行为放这 */
  ],
);

const handleRefresh = useCallback((): void => {
  loadData();
}, [loadData]);

// useEffect 紧挨 return 前
useEffect(() => {
  loadData();
}, [loadData]);
```

**数组场景**：state 初值 `[]`（不是 undefined），catch 内 `setData([])`：

```tsx
const [items, setItems] = useState<API.XxxItem[]>([]);
// ...
setItems(res.data ?? []);
// catch: setItems([])
```

**关键映射**（旧 `useRequest` 配置 → 新模式）：

| 旧                                | 新                                                        |
| --------------------------------- | --------------------------------------------------------- |
| `useRequest(fn)` 自动 mount 触发  | `useEffect(() => { loadFn() }, [loadFn])`                 |
| `refreshDeps: [a, b]`             | 把 `[a, b]` 放到 `loadFn = useCallback(..., [a, b])` 依赖 |
| `formatResult: (res) => res.data` | try 内直接 `setData(res.data)`                            |
| `manual: true`                    | 不写 useEffect，只在用户触发时调 `loadFn`                 |
| `refresh()`                       | `handleRefresh = useCallback(() => loadFn(), [loadFn])`   |
| `pollingInterval`                 | useEffect 内 `setInterval` 手动管理                       |

**参考实现**：`src/pages/sampleprogress/index.tsx` / `src/pages/recommendpnl/components/TrackDetailDrawer.tsx`。

**grep 自检**：

```bash
grep -rn "useRequest" src/pages src/components
# 业务代码应 0 命中（src/services/apis/ 是 auto-gen 不在统计内）
```

事故复盘：2026-05-21 sampleprogress 页面显示 0/0/0 全 bug，根因即 `useRequest` + ahooks v3 + 自定义 fetch 三层组合下 data 永远不落地。

---

## Hooks 书写顺序

```typescript
const MyComponent: React.FC = () => {
  // 1. useRef / useForm
  // 2. useState
  // 3. useModel / useContext
  // 4. useCallback / useMemo
  // 5. 普通函数（不依赖 Hook）
  // 6. useEffect ← 必须紧挨 return 前

  return (...);
};
```

---

## JSX 中禁止内联函数（`react/jsx-no-bind`）

`react/jsx-no-bind` 会**追溯变量定义**——组件内用 `const fn = () => ...` 定义的箭头函数，即便提前定义再传引用，也会被识别为每次渲染新建，仍报错。满足规则的方式：

```typescript
// ❌ 内联定义 → 报错
<UserDrawer onCancel={() => setOpen(false)} />

// ❌ 组件内提前定义箭头函数再传引用 → 仍报错（规则追溯定义）
const handleClose = () => setOpen(false);
<UserDrawer onCancel={handleClose} />

// ✅ useCallback → 通过
const handleClose = useCallback(() => setOpen(false), []);
<UserDrawer onCancel={handleClose} />

// ✅ 原生 DOM 标签豁免（ignoreDOMComponents=true）→ 普通函数即可
const handleClick = (e: React.MouseEvent) => { doSomething(e); };
<div onClick={handleClick} />
```

`.map()` 闭包场景提取子组件，在子组件内用 `useCallback`：

```typescript
const VariableInput: React.FC<{ name: string; onChange: (name: string, val: string) => void }> = ({ name, onChange }) => {
  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => onChange(name, e.target.value), [name, onChange]);
  return <Input onChange={handleChange} />;
};

{variables.map((v) => <VariableInput key={v} name={v} onChange={handleVariableChange} />)}
```

---

## `useCallback` 判断原则

| 场景                                        | 是否需要 `useCallback`                          |
| ------------------------------------------- | ----------------------------------------------- |
| 传给任意自定义 / antd 组件的函数 props      | ✅ 需要（`react/jsx-no-bind` 追溯箭头函数定义） |
| 作为其他 `useCallback` / `useMemo` 的依赖项 | ✅ 需要                                         |
| 传给原生 DOM 标签（div/span/button 等）     | ❌ 普通函数即可（`ignoreDOMComponents` 豁免）   |
| 仅在组件内部调用，不传给任何 JSX props      | ❌ 普通函数即可                                 |
| 外部导入的具名函数（如 `toolBarRender`）    | ❌ 不需要 `useCallback`                         |

---

## 组件间数据传递规范

**数据**（状态、引用）通过 `context` 对象传递，**回调**平铺为 `on****`：

```typescript
// index.tsx
const DRAWER_DEFAULT = { open: false, mode: 'create' as const };
const [drawerContext, setDrawerContext] = useState(DRAWER_DEFAULT);

const handleEdit = useCallback((record: RecordType) => {
  setDrawerContext({ open: true, mode: 'edit', record });
}, []);
const handleClose = useCallback(() => setDrawerContext(DRAWER_DEFAULT), []);

<UserDrawer context={drawerContext} onOk={handleOk} onCancel={handleClose} />

// UserDrawer/index.tsx
interface UserDrawerContext { open: boolean; mode: 'create' | 'edit'; record?: RecordType; }
interface UserDrawerProps { context: UserDrawerContext; onOk: () => void; onCancel: () => void; }

const UserDrawer: React.FC<UserDrawerProps> = ({ context, onOk, onCancel }) => {
  const { open, mode, record } = context;
};
```

弹出层回调统一用 `onOk` / `onCancel`，禁止 `onSuccess`、`onConfirm`、`onClose`。

**`context` 类型从子组件导出，父组件直接引用**，避免重复定义：

```typescript
// UserDrawer/index.tsx
export interface UserDrawerContext {
  open: boolean;
  mode: 'create' | 'edit';
  record?: RecordType;
}

// index.tsx
import UserDrawer, { type UserDrawerContext } from './components/UserDrawer';
const DRAWER_DEFAULT: UserDrawerContext = { open: false, mode: 'create' };
```

---

## `useEffect` 保持简洁

`useEffect` 只做两件事：**判断触发条件 + 调用提取好的函数**，不写具体逻辑。

```typescript
// ✅ useEffect 只负责触发
const loadDetail = useCallback(async () => {
  if (!record?.nodeCode) return;
  setLoading(true);
  try {
    const res = await postDetail({ nodeCode: record.nodeCode });
    setDetail(res.data);
  } finally {
    setLoading(false);
  }
}, [record]);

useEffect(() => {
  if (open && record?.nodeCode) {
    loadDetail();
  } else {
    setDetail(undefined);
  }
}, [open, record, loadDetail]);

// ❌ useEffect 里写内联请求逻辑
useEffect(() => {
  if (open && record?.nodeCode) {
    setLoading(true);
    postDetail({ nodeCode: record.nodeCode })
      .then((res) => setDetail(res.data))
      .finally(() => setLoading(false));
  }
}, [open, record]);
```

需要多处调用（如初始加载 + 翻页）时，提取为带参数的 `useCallback`：

```typescript
const loadLogs = useCallback(async (pageNum: number) => { ... }, [nodeCode]);

const initLogs = useCallback(() => {
  setLogData((prev) => ({ ...prev, page: 1 }));
  loadLogs(1);
}, [loadLogs]);

const handlePageChange = useCallback((p: number) => {
  setLogData((prev) => ({ ...prev, page: p }));
  loadLogs(p);
}, [loadLogs]);

useEffect(() => {
  if (open && nodeCode) {
    initLogs();
  } else {
    resetLogs();
  }
}, [open, nodeCode, initLogs, resetLogs]);
```

---

## 关联 state 合并

来自同一数据源或同一关注点的状态，合并为一个 `useState` 对象，避免多次 setState 产生中间渲染状态：

```typescript
// ✅ 同一接口返回的数据 + 分页页码合并
const [logData, setLogData] = useState<{ list: LogRecord[]; total: number; page: number }>({
  list: [],
  total: 0,
  page: 1,
});

// 更新时用展开保留其他字段
setLogData((prev) => ({ ...prev, list: res.data ?? [], total: res.total ?? 0 }));

// 重置时一次性清空
setLogData({ list: [], total: 0, page: 1 });

// ❌ 拆成三个独立 state
const [logs, setLogs] = useState<LogRecord[]>([]);
const [total, setTotal] = useState(0);
const [page, setPage] = useState(1);
```

**判断是否合并的原则**：总是一起更新、一起重置 → 合并；各自独立变化 → 分开。

---

## 错误处理

异步操作加 `try-catch`，后端有统一错误处理，`catch` 内无需重复提示：

```typescript
try {
  const res = await getUsers();
  setUsers(res.data);
} catch {
  // 无需处理
}
```
