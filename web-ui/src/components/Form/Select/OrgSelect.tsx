// 顶栏组织选择器 —— @jlogi/ui Select + fetchOptions 远程拉组织选项(DemoSelect 模式, 纯展示无副作用)。
// 受控值绑 useModel('org').currentOrgId; 默认值/初始化由 org model 负责(全局常驻必跑), 本组件不在 fetchOptions 里设默认。
// 切换写 localStorage 注入 X-Org-Id, 并重置项目(切 org 后 project model 按新 org 重新默认第一个)。
import { useCallback } from 'react';
import { useModel } from '@umijs/max';

import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';

import { postOrgsList } from '@/services/apis/orgapi';

type OrgSelectProps = Omit<SelectProps, 'fetchOptions'>;

const OrgSelect: React.FC<OrgSelectProps> = ({ ...restProps }) => {
  const { currentOrgId, setCurrentOrg } = useModel('org');
  const { setCurrentProject } = useModel('project');

  const handleFetchOptions = useCallback(
    async (params: { keyWord: string; page: number; pageSize: number }) => {
      try {
        const result = await postOrgsList({ pageNumber: 1, pageSize: 200 });
        const list = result?.data ?? [];
        const options = list.map((item) => ({
          value: item.code ?? '',
          label: item.name ?? item.code ?? '',
          title: item.name ?? '',
          code: item.code ?? '',
          name: item.name ?? '',
          data: item,
        }));
        const filteredOptions = params.keyWord
          ? options.filter(
            (opt) =>
              opt.code.toLowerCase().includes(params.keyWord.toLowerCase()) ||
                opt.name.toLowerCase().includes(params.keyWord.toLowerCase()),
          )
          : options;
        return { data: filteredOptions, totalCounts: filteredOptions.length };
      } catch {
        return { data: [], totalCounts: 0 };
      }
    },
    [],
  );

  const handleChange = useCallback(
    (value: string): void => {
      setCurrentOrg(value); // 写 localStorage(current_org), fetch 据此注入 X-Org-Id
      setCurrentProject(''); // 切 org 重置项目(ProjectSelect 会按新 org 重新 fetch)
    },
    [setCurrentOrg, setCurrentProject],
  );

  return (
    <Select
      hideHeader
      hideCodeColumn
      placeholder={restProps.placeholder ?? '选择组织'}
      style={restProps.style ?? { width: '100%' }}
      fetchOptions={handleFetchOptions}
      value={currentOrgId || undefined}
      onChange={handleChange}
      allowClear={false}
      showSearchPanel={false}
      showPagination={false}
      columnsWidth={[80, 120]}
      {...restProps}
    />
  );
};

export default OrgSelect;
