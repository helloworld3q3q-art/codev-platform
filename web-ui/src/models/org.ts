// 当前组织上下文 model —— 选中 org 写 localStorage, fetch 拦截器据此注入 X-Org-Id。
import { useCallback, useEffect, useState } from 'react';

import { postOrgsList } from '@/services/apis/orgapi';

const STORAGE_KEY = 'current_org';

export interface OrgOption {
  code: string;
  name: string;
}

export default function useOrgModel() {
  const [orgs, setOrgs] = useState<OrgOption[]>([]);
  const [currentOrgId, setCurrentOrgId] = useState<string>(
    () => localStorage.getItem(STORAGE_KEY) ?? '',
  );

  const loadOrgs = useCallback(async (): Promise<void> => {
    try {
      const res = await postOrgsList({ pageNumber: 1, pageSize: 200 });
      const list: OrgOption[] = (res.data ?? []).map((o) => ({
        code: o.code ?? '',
        name: o.name ?? '',
      }));
      setOrgs(list);
    } catch {
      setOrgs([]);
    }
  }, []);

  const setCurrentOrg = useCallback((code: string): void => {
    if (code) {
      localStorage.setItem(STORAGE_KEY, code);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
    setCurrentOrgId(code);
  }, []);

  useEffect(() => {
    loadOrgs();
  }, [loadOrgs]);

  return { orgs, currentOrgId, setCurrentOrg, loadOrgs };
}
