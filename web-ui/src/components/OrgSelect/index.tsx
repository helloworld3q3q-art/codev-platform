// 顶栏组织选择器 —— 切 org 写 useModel('org'), 并重置/重拉项目 (项目按 org 过滤)。
import { useCallback, useMemo } from 'react';
import { useModel } from '@umijs/max';

import { Select } from 'antd';

const OrgSelect: React.FC = () => {
  const { orgs, currentOrgId, setCurrentOrg } = useModel('org');
  const { loadProjects, setCurrentProject } = useModel('project');

  const options = useMemo(
    () => orgs.map((o) => ({ label: o.name || o.code, value: o.code })),
    [orgs],
  );

  const handleChange = useCallback(
    (value: string): void => {
      setCurrentOrg(value); // 同步写 localStorage(current_org), fetch 立即据此注入 X-Org-Id
      setCurrentProject(''); // 切 org 重置项目选择
      loadProjects(); // 重拉该 org 下的项目
    },
    [setCurrentOrg, setCurrentProject, loadProjects],
  );

  return (
    <Select
      className="w-160"
      placeholder="选择组织"
      value={currentOrgId || undefined}
      onChange={handleChange}
      options={options}
    />
  );
};

export default OrgSelect;
