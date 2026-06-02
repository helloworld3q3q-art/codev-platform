import type { ParamsType, ProTableProps } from '@ant-design/pro-components';
import { ProTable } from '@ant-design/pro-components';
import { Badge, Spin, Tabs } from 'antd';
import React, { useCallback, useState } from 'react';
import styles from './index.less';

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
}

function Table<
  T extends Record<string, any> = any,
  U extends ParamsType = Record<string, any>,
  ValueType = 'text',
>(props: TableProps<T, U, ValueType>) {
  const {
    columns,
    dataSource,
    tabs = [],
    activeTab,
    onTabChange,
    tabsCount = [],
    tabsCountColors = colors,
    className,
    request,
    pagination,
    ...restProps
  } = props;
  const [tabLoading, setTabLoading] = useState<boolean>(false);

  const tabChange = useCallback(
    (key: string) => {
      if (tabLoading) return;
      setTabLoading(true);
      if (onTabChange) {
        onTabChange(key);
      }
    },
    [tabLoading, onTabChange],
  );
  const containerClass = styles.container;

  // 处理分页参数转换
  const handleRequest = useCallback(
    async (params: any, sort: any, filter: any) => {
      if (!request) return { data: [], success: true, total: 0 };
      // 将 current 转换为 pageNumber
      const apiParams = { ...params, pageNumber: params.current };
      delete apiParams.current;

      setTabLoading(true);
      const res = await request(apiParams, sort, filter);
      setTabLoading(false);
      return res;
    },
    [request],
  );

  const tableRender = useCallback(
    (
      _: unknown,
      _dom: React.ReactNode,
      domList: { toolbar?: React.ReactNode; alert?: React.ReactNode; table?: React.ReactNode },
    ) => (
      <div className={className ? className : containerClass}>
        {domList.toolbar}
        {domList.alert}

        <Spin spinning={tabLoading}>
          {tabs.length > 0 && (
            <Tabs
              activeKey={activeTab}
              onChange={tabChange}
              items={tabs.map((tab, index) => ({
                key: tab.key,
                label: (
                  <span>
                    {tab.label}
                    <Badge
                      count={tabsCount[index]}
                      style={{
                        backgroundColor: tabsCountColors[index],
                        marginLeft: 6,
                      }}
                    />
                  </span>
                ),
              }))}
            />
          )}
          {domList.table}
        </Spin>
      </div>
    ),
    [className, containerClass, tabLoading, tabs, activeTab, tabChange, tabsCount, tabsCountColors],
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
      rowKey="key"
      className={`${styles.table} ${props.className || ''}`}
      defaultSize="small"
      request={handleRequest}
      pagination={defaultPagination}
      tableRender={tableRender}
      {...restProps}
    />
  );
}

export default Table;
