// 当前项目上下文 model —— 多租户:选中项目写 localStorage，fetch 拦截器据此注入 X-Project-Id。
import { useCallback, useEffect, useState } from 'react';

import { postProjectsList } from '@/services/apis/projectapi';

const STORAGE_KEY = 'current_project';

export interface ProjectOption {
  code: string;
  name: string;
}

export default function useProjectModel() {
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string>(
    () => localStorage.getItem(STORAGE_KEY) ?? '',
  );

  const loadProjects = useCallback(async (): Promise<void> => {
    try {
      const res = await postProjectsList({ pageNumber: 1, pageSize: 200 });
      const list: ProjectOption[] = (res.data ?? []).map((p) => ({
        code: p.code ?? '',
        name: p.name ?? '',
      }));
      setProjects(list);
      // 未选且有项目时默认选第一个，保证多租户接口始终带项目头。
      setCurrentProjectId((prev) => {
        if (prev) return prev;
        const first = list[0]?.code ?? '';
        if (first) localStorage.setItem(STORAGE_KEY, first);
        return first;
      });
    } catch {
      setProjects([]);
    }
  }, []);

  const setCurrentProject = useCallback((code: string): void => {
    if (code) {
      localStorage.setItem(STORAGE_KEY, code);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
    setCurrentProjectId(code);
  }, []);

  useEffect(() => {
    loadProjects();
  }, [loadProjects]);

  return { projects, currentProjectId, setCurrentProject, loadProjects };
}
