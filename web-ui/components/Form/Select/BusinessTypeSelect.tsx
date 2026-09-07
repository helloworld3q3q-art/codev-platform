import { postOptions } from '@/services/apis/businesstypeapi';
import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';
import React, { useCallback, useRef } from 'react';

interface BusinessTypeSelectProps extends Omit<SelectProps, 'fetchOptions'> {
  extraParams?: Record<string, unknown>;
}

/**
 * 业务类型选择器
 */
const BusinessTypeSelect: React.FC<BusinessTypeSelectProps> = ({ extraParams, ...restProps }) => {
  const extraParamsRef = useRef(extraParams);
  extraParamsRef.current = extraParams;

  const handleFetchOptions = useCallback(
    async (params: { keyWord: string; page: number; pageSize: number }) => {
      try {
        const keyword = params.keyWord?.trim();
        const result = await postOptions({
          pageNumber: params.page ?? 1,
          pageSize: params.pageSize ?? 10,
          keyword: keyword || undefined,
          ...(extraParamsRef.current ?? {}),
        });

        const list = result?.data ?? [];
        const options = list.map((item) => ({
          value: item.code ?? '',
          label: item.name ?? item.code ?? '',
          title: item.name ?? item.code ?? '',
          code: item.code,
          name: item.name ?? item.code ?? '',
          data: item,
        }));

        return { data: options, totalCounts: options.length };
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
      placeholder={restProps.placeholder ?? '请选择业务类型'}
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

export default BusinessTypeSelect;
