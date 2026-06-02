// 顶栏项目选择器 —— 写当前项目到 useModel('project')，fetch 拦截器据此注入 X-Project-Id。
import { useCallback, useMemo } from 'react';
import { useModel } from '@umijs/max';

import { Select } from 'antd';

const ProjectSelect: React.FC = () => {
  const { projects, currentProjectId, setCurrentProject } = useModel('project');

  const options = useMemo(
    () => projects.map((p) => ({ label: p.name || p.code, value: p.code })),
    [projects],
  );

  const handleChange = useCallback(
    (value: string): void => {
      setCurrentProject(value);
    },
    [setCurrentProject],
  );

  return (
    <Select
      className="w-200"
      placeholder="选择项目"
      value={currentProjectId || undefined}
      onChange={handleChange}
      options={options}
    />
  );
};

export default ProjectSelect;
