import { postBusinessNodesPage } from '@/services/apis/businessnodeapi';
import { postPromptsPage } from '@/services/apis/promptapi';
import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';
import React, { useCallback, useRef } from 'react';

interface PromptSelectProps extends Omit<SelectProps, 'fetchOptions'> {
  extraParams?: Record<string, unknown>;
}

/**
 * 提示词选择器（仅已启用）
 */
const PromptSelect: React.FC<PromptSelectProps> = ({ extraParams, ...restProps }) => {
  const extraParamsRef = useRef(extraParams);
  extraParamsRef.current = extraParams;

  const handleFetchOptions = useCallback(
    async (params: { keyWord: string; page: number; pageSize: number }) => {
      try {
        const result = await postPromptsPage({
          pageNumber: 1,
          pageSize: 20,
          status: 'Active',
          ...(extraParamsRef.current ?? {}),
        });

        const list = result?.data ?? [];
        const businessNodeResult = await postBusinessNodesPage({
          pageNumber: 1,
          pageSize: 200,
        });
        const businessNodeMap = (businessNodeResult.data ?? []).reduce(
          (acc, item) => {
            if (item.nodeCode) {
              acc[item.nodeCode] = item.nodeName ?? item.nodeCode;
            }
            return acc;
          },
          {} as Record<string, string>,
        );
        const options = list.map((item) => ({
          value: item.promptCode ?? '',
          label: `${item.promptCode} - ${businessNodeMap[item.businessNodeCode ?? ''] ?? item.businessNodeCode ?? ''}`,
          title: item.promptCode ?? '',
          code: item.promptCode,
          name: item.promptCode ?? '',
          data: item,
        }));

        const filteredOptions = params.keyWord
          ? options.filter(
              (opt) =>
                opt.code?.toLowerCase().includes(params.keyWord.toLowerCase()) ||
                opt.label?.toLowerCase().includes(params.keyWord.toLowerCase()),
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
      placeholder={restProps.placeholder ?? '选择提示词（仅已启用）'}
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

export default PromptSelect;
