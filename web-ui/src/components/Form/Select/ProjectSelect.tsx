// 顶栏项目选择器 —— @jlogi/ui Select + fetchOptions 远程拉项目选项(按当前 org 过滤, DemoSelect 模式, 纯展示无副作用)。
// 受控值绑 useModel('project').currentProjectId; 切换写 localStorage 注入 X-Project-Id。
// 默认值/初始化(未选时默认第一个项目, 保证 X-Project-Id 恒有)由 project model 负责, 不在 fetchOptions 里设默认。
import { useCallback } from 'react';
import { useModel } from '@umijs/max';

import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';

import { postProjectsList } from '@/services/apis/projectapi';

type ProjectSelectProps = Omit<SelectProps, 'fetchOptions'>;

const ProjectSelect: React.FC<ProjectSelectProps> = ({ ...restProps }) => {
  const { currentProjectId, setCurrentProject } = useModel('project');

  const handleFetchOptions = useCallback(
    async (params: { keyWord: string; page: number; pageSize: number }) => {
      try {
        const result = await postProjectsList({ pageNumber: 1, pageSize: 200 });
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
      setCurrentProject(value);
    },
    [setCurrentProject],
  );

  return (
    <Select
      hideHeader
      hideCodeColumn
      placeholder={restProps.placeholder ?? '选择项目'}
      style={restProps.style ?? { width: '100%' }}
      fetchOptions={handleFetchOptions}
      value={currentProjectId || undefined}
      onChange={handleChange}
      allowClear={false}
      showSearchPanel={false}
      showPagination={false}
      columnsWidth={[80, 120]}
      {...restProps}
    />
  );
};

export default ProjectSelect;
