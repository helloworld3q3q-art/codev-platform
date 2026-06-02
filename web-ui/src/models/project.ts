// 当前项目上下文 model —— 多租户:只持有当前选择 + 切换副作用(写 localStorage, fetch 拦截器据此注入 X-Project-Id)。
// 项目列表数据由 ProjectSelect 的 fetchOptions 远程拉取(按当前 org 过滤, DemoSelect 模式), 不在 model 缓存。
import { useCallback, useState } from 'react';

const STORAGE_KEY = 'current_project';

export default function useProjectModel() {
  const [currentProjectId, setCurrentProjectId] = useState<string>(
    () => localStorage.getItem(STORAGE_KEY) ?? '',
  );

  const setCurrentProject = useCallback((code: string): void => {
    if (code) {
      localStorage.setItem(STORAGE_KEY, code);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
    setCurrentProjectId(code);
  }, []);

  return { currentProjectId, setCurrentProject };
}
