import type { ParamsType, ProTableProps } from '@ant-design/pro-components';
import { ProTable } from '@ant-design/pro-components';
//import { Badge, Spin, Tabs } from 'antd';
import {
  createApiWithTransform,
  FieldType,
  requestWrapper as jRequestWrapper,
  type AdvancedSearchData,
  type ApiPageResponse,
  type ConditionItem,
  type ProColumns,
  type RequestData,
  type SortOrder,
} from '@jlogi/ui';
import React, { useCallback } from 'react';
import styles from './index.module.less';

export const colors: string[] = [
  '#1890ff', // 蓝色
  '#eab308', // 黄色
  '#a855f7', // 紫色
  '#3b82f6', // 蓝色
  '#22c55e', // 绿色
  '#ef4444', // 红色
  '#6d7077', // 灰色
];

export interface StatusTab {
  key: string;
  label: React.ReactNode;
}

export interface TableProps<
  T extends Record<string, any> = any,
  U extends ParamsType = Record<string, any>,
  ValueType = 'text',
> extends ProTableProps<T, U, ValueType> {
  tabs?: StatusTab[];
  tabsCount?: string[] | number[];
  activeTab?: string;
  onTabChange?: (key: string) => void;
  tabsCountColors?: string[];
  containerClassName?: string;
}

function Table<
  T extends Record<string, any> = any,
  U extends ParamsType = Record<string, any>,
  ValueType = 'text',
>(props: TableProps<T, U, ValueType>) {
  const {
    columns,
    dataSource,
    //  tabs = [],
    //activeTab,
    //onTabChange,
    //tabsCount = [],
    //tabsCountColors = colors,
    request,
    pagination,
    rowKey = 'key',
    //containerClassName,
    scroll,
    ...restProps
  } = props;

  // 仅在外部传了 request 时才包装；否则不能注入空 handleRequest，
  // 否则 ProTable 会优先用 request 的 [] 覆盖 dataSource，导致纯 dataSource 模式（如 dashboard）渲染不出数据
  const handleRequest = useCallback(
    async (params: any, sort: any, filter: any) => {
      if (!request) return { data: [], success: true, total: 0 };
      const res = await request(params, sort, filter);
      return res;
    },
    [request],
  );

  // 默认分页配置（支持通过 pagination={false} 关闭分页）
  const defaultPagination =
    pagination === false
      ? false
      : {
        defaultPageSize: 10,
        showQuickJumper: true,
        showSizeChanger: true,
        showTotal: (total: number) => `共 ${total} 条`,
        ...(pagination || {}),
      };

  return (
    <ProTable<T, U, ValueType>
      columns={columns}
      dataSource={dataSource}
      rowKey={rowKey}
      className={`${styles.table} ${props.className || ''}`}
      request={request ? handleRequest : undefined}
      pagination={defaultPagination}
      scroll={scroll}
      {...restProps}
    />
  );
}

// 项目特定的请求包装函数，主要用于数据转换
export const customRequestWrapper = async <U = Record<string, any>,>(
  params: U & {
    pageSize?: number;
    current?: number;
    keyword?: string;
  },
  sort: Record<string, SortOrder>,
  filter: Record<string, (string | number)[] | null>,
  apiFunction: (params: any) => Promise<any>,
  convertParamsFunction?: (params: any) => any,
): Promise<RequestData<any>> => {
  // ProTable 用 `current` 表示页码，各模块 convertParams 读 `pageNum`；
  // 在此统一映射，避免 DuplicateRequestManager 把不同页请求当重复合并
  const normalizedParams = {
    ...params,
    pageNum: (params as any).current ?? (params as any).pageNum,
  };

  // 使用 createApiWithTransform 包装 API 函数，应用默认的数据转换逻辑
  const transformedApi = createApiWithTransform(
    apiFunction,
    (response: ApiPageResponse): RequestData<any> => {
      const raw = response as unknown as Record<string, any>;
      const rawData = raw?.data;

      // 后端返回扁平结构：data 直接是行数组
      const rows: any[] = Array.isArray(rawData) ? rawData : [];
      const totalFromTop = typeof raw.total === 'number' ? raw.total : undefined;
      const total = totalFromTop ?? rows.length;
      // 后端业务码：0 = 成功（与 fetch.ts responseCodeHandler 对齐）；
      // 兼容部分接口返回 ok=true 或 result=200 的历史写法
      const success = raw.result === 0 || raw.ok === true || raw.result === 200;

      return { data: rows, success, total };
    },
  );

  return jRequestWrapper(normalizedParams, sort, filter, transformedApi, convertParamsFunction);
};

export default Table;
export {
  FieldType,
  customRequestWrapper as requestWrapper,
  type ProColumns,
  type AdvancedSearchData,
  type ConditionItem,
};
