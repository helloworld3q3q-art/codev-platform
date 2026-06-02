// 当前项目上下文 model —— 单一来源: 持有当前选择 + 初始化默认(全局常驻必跑, 与下拉是否打开无关)。
// 切换写 localStorage(fetch 拦截器据此注入 X-Project-Id)。项目列表选项仍由 ProjectSelect 的 fetchOptions 远程拉
// (按当前 org 过滤, DemoSelect 模式), 但"默认选第一个项目"由本 model 的 useEffect 负责。
// 级联: 切 org 时由 OrgSelect(React 组件, 可同时用两个 model)调 setCurrentProject(''),
// 本 model useEffect 监听到空值即按新 org 重新默认第一个项目 —— 不需 model 内嵌套 useModel('org')。
import { useCallback, useEffect, useState } from 'react';

import { postProjectsList } from '@/services/apis/projectapi';

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

  // 初始化 + 级联默认: 未选项目时拉当前 org 下首个项目, 保证多租户接口恒带 X-Project-Id。
  // 拉取经 fetch 拦截器自动带 X-Org-Id, 故按当前 org 过滤; 切 org 置空后会再次进入这里按新 org 默认。
  const ensureDefaultProject = useCallback(async (): Promise<void> => {
    if (currentProjectId) {
      return;
    }
    try {
      const res = await postProjectsList({ pageNumber: 1, pageSize: 1 });
      const firstProject = res.data?.[0]?.code;
      if (firstProject) {
        setCurrentProject(firstProject);
      }
    } catch {
      // 拉项目失败不阻塞
    }
  }, [currentProjectId, setCurrentProject]);

  useEffect(() => {
    ensureDefaultProject();
  }, [ensureDefaultProject]);

  return { currentProjectId, setCurrentProject };
}
