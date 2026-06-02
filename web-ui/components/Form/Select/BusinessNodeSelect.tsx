import { postBusinessNodesPage } from '@/services/apis/businessnodeapi';
import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';
import React, { useCallback, useRef } from 'react';

interface BusinessNodeSelectProps extends Omit<SelectProps, 'fetchOptions'> {
  /** 业务类型编码，变化时自动重新加载节点列表 */
  businessTypeCode?: string;
  extraParams?: Record<string, unknown>;
}

/**
 * 业务节点选择器
 * 依赖 businessTypeCode，businessTypeCode 变化时自动重新加载
 */
const BusinessNodeSelect: React.FC<BusinessNodeSelectProps> = ({
  businessTypeCode,
  extraParams,
  ...restProps
}) => {
  const extraParamsRef = useRef(extraParams);
  extraParamsRef.current = extraParams;

  const handleFetchOptions = useCallback(
    async (params: { keyWord: string; page: number; pageSize: number }) => {
      try {
        const result = await postBusinessNodesPage({
          pageNumber: 1,
          pageSize: 20,
          ...(businessTypeCode ? { businessTypeCode } : {}),
          ...(extraParamsRef.current ?? {}),
        });

        const list = result?.data ?? [];
        const options = list.map((item) => ({
          value: item.nodeCode ?? '',
          label: item.nodeName ?? item.nodeCode ?? '',
          title: item.nodeName ?? item.nodeCode ?? '',
          code: item.nodeCode,
          name: item.nodeName ?? item.nodeCode ?? '',
          data: item,
        }));

        const filteredOptions = params.keyWord
          ? options.filter(
              (opt) =>
                opt.code?.toLowerCase().includes(params.keyWord.toLowerCase()) ||
                opt.name?.toLowerCase().includes(params.keyWord.toLowerCase()),
            )
          : options;

        return { data: filteredOptions, totalCounts: filteredOptions.length };
      } catch {
        return { data: [], totalCounts: 0 };
      }
    },
    [businessTypeCode],
  );

  return (
    <Select
      hideHeader
      hideCodeColumn
      placeholder={restProps.placeholder ?? '请先选择业务类型'}
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

export default BusinessNodeSelect;
