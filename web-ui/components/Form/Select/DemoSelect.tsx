/* eslint-disable */
// @ts-nocheck
import { postSelectPositionList } from '@/services/apis/position';
import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';
import React, { useCallback } from 'react';

interface DemoSelectProps extends Omit<SelectProps, 'fetchOptions'> {
  /**
   * 额外的查询参数
   */
  extraParams?: Record<string, any>;
}

/**
 * 岗位选择器
 * 用于选择岗位
 */
const DemoSelect: React.FC<DemoSelectProps> = ({ extraParams = {}, ...restProps }) => {
  // 获取岗位数据
  const handleFetchOptions = useCallback(
    async (params: { keyWord: string; page: number; pageSize: number }) => {
      try {
        const result = await postSelectPositionList({
          ...extraParams,
        });

        const list: API.PositionVo[] = result?.data || [];
        const options = list.map((item) => ({
          value: item.positionName || '',
          label: item.positionName || '',
          title: item.positionName || '',
          code: item.positionName,
          name: item.positionName || '',
          data: item,
        }));

        // 根据关键词过滤
        const filteredOptions = params.keyWord
          ? options.filter(
              (opt) =>
                opt.code?.toLowerCase().includes(params.keyWord.toLowerCase()) ||
                opt.name?.toLowerCase().includes(params.keyWord.toLowerCase()),
            )
          : options;

        return {
          data: filteredOptions,
          totalCounts: filteredOptions.length,
        };
      } catch (error) {
        console.error('获取岗位数据失败:', error);
        return { data: [], totalCounts: 0 };
      }
    },
    [extraParams],
  );

  return (
    <Select
      labelInValue
      hideHeader
      showSelectAllButtons
      hideCodeColumn
      placeholder={restProps.placeholder ?? '请选择岗位'}
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

export default DemoSelect;
