// 表单 / 筛选用组织选择器 —— 受控 value/onChange 由调用方(Form.Item / ProTable formItemRender)提供。
// 与顶栏 OrgSelect 区别: 不绑全局 useModel('org'), 纯远程下拉, 可在注册表单 / 列表筛选复用。
import React, { useCallback } from 'react';

import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';

import { postOrgsList } from '@/services/apis/orgapi';

type OrgFieldSelectProps = Omit<SelectProps, 'fetchOptions'>;

const OrgFieldSelect: React.FC<OrgFieldSelectProps> = ({ ...restProps }) => {
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

  return (
    <Select
      hideHeader
      hideCodeColumn
      placeholder={restProps.placeholder ?? '选择组织'}
      style={restProps.style ?? { width: '100%' }}
      fetchOptions={handleFetchOptions}
      allowClear={restProps.allowClear ?? true}
      showSearchPanel={false}
      showPagination={false}
      columnsWidth={[80, 120]}
      {...restProps}
    />
  );
};

export default OrgFieldSelect;
