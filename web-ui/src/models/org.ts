// 当前组织上下文 model —— 只持有当前选择 + 切换副作用(写 localStorage, fetch 拦截器据此注入 X-Org-Id)。
// 组织列表数据由 OrgSelect 的 fetchOptions 远程拉取(DemoSelect 模式), 不在 model 缓存。
import { useCallback, useState } from 'react';

const STORAGE_KEY = 'current_org';

export default function useOrgModel() {
  const [currentOrgId, setCurrentOrgId] = useState<string>(
    () => localStorage.getItem(STORAGE_KEY) ?? '',
  );

  const setCurrentOrg = useCallback((code: string): void => {
    if (code) {
      localStorage.setItem(STORAGE_KEY, code);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
    setCurrentOrgId(code);
  }, []);

  return { currentOrgId, setCurrentOrg };
}
