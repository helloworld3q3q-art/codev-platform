// 当前组织上下文 model —— 单一来源: 持有当前选择 + 初始化默认(全局常驻必跑, 与下拉是否打开无关)。
// 切换写 localStorage(fetch 拦截器据此注入 X-Org-Id)。组织列表选项仍由 OrgSelect 的 fetchOptions 远程拉(DemoSelect 模式),
// 但"默认选第一个组织"由本 model 的 useEffect 负责, 不再依赖下拉懒渲染。
import { useCallback, useEffect, useState } from 'react';

import { postOrgsList } from '@/services/apis/orgapi';

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

  // 初始化: 未选组织时拉列表默认第一个(getInitialState 已用会话 org 预置 localStorage 时这里直接跳过)。
  const ensureDefaultOrg = useCallback(async (): Promise<void> => {
    if (currentOrgId) {
      return;
    }
    try {
      const res = await postOrgsList({ pageNumber: 1, pageSize: 1 });
      const firstOrg = res.data?.[0]?.code;
      if (firstOrg) {
        setCurrentOrg(firstOrg);
      }
    } catch {
      // 拉组织失败不阻塞
    }
  }, [currentOrgId, setCurrentOrg]);

  useEffect(() => {
    ensureDefaultOrg();
  }, [ensureDefaultOrg]);

  return { currentOrgId, setCurrentOrg };
}
