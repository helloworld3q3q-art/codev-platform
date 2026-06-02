// 顶栏项目选择器 —— @jlogi/ui Select + fetchOptions 远程拉项目(按当前 org 过滤, DemoSelect 模式)。
// 受控值绑 useModel('project').currentProjectId; 切换写 localStorage 注入 X-Project-Id。
// 未选且有项目时默认选第一个, 保证多租户接口始终带项目头(经 ref 读取当前值, 避免进 fetchOptions 依赖触发重拉)。
import { useCallback, useRef } from 'react';
import { useModel } from '@umijs/max';

import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';

import { postProjectsList } from '@/services/apis/projectapi';

type ProjectSelectProps = Omit<SelectProps, 'fetchOptions'>;

const ProjectSelect: React.FC<ProjectSelectProps> = ({ ...restProps }) => {
  const { currentProjectId, setCurrentProject } = useModel('project');

  // 用 ref 读取当前选择, 不进 handleFetchOptions 依赖(否则选中变化 → 函数重建 → Select 重拉, 易循环)。
  const currentProjectIdRef = useRef(currentProjectId);
  currentProjectIdRef.current = currentProjectId;

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
        // 初次加载(无关键词)且未选时, 默认选第一个项目, 保证 X-Project-Id 恒有。
        if (!params.keyWord && !currentProjectIdRef.current && options.length > 0) {
          setCurrentProject(options[0].value);
        }
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
    [setCurrentProject],
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
