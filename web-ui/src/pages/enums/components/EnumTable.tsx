import type { ProColumns } from '@ant-design/pro-components';
import { ProCard } from '@ant-design/pro-components';
import { Tag } from 'antd';
import React from 'react';

import ResizableTable from '@/components/ResizableTable';
import type { EnumItemDTO } from '@/models/enum';

interface EnumTableProps {
  enumType: string;
  items: EnumItemDTO[];
}

// 列定义集中在模块级，避免每次渲染重建（也不在 index 内联）
const renderValueTag = (value?: string): React.ReactNode => {
  return <Tag>{value ?? '-'}</Tag>;
};

const COLUMNS: ProColumns<EnumItemDTO>[] = [
  { title: 'value', dataIndex: 'enumValue', search: false, render: (_, record) => renderValueTag(record.enumValue) },
  { title: '中文', dataIndex: 'localLanguage', search: false },
  { title: '排序', dataIndex: 'enumOrder', width: 80, search: false },
  { title: '说明', dataIndex: 'description', search: false },
];

// 单个枚举类型渲染一个只读展示表；用项目封装版 ResizableTable 的纯 dataSource 模式
// （不传 request，禁用搜索/工具栏/分页，符合 component-patterns: 禁直接用 antd Table）
const EnumTable: React.FC<EnumTableProps> = ({ enumType, items }) => {
  return (
    <ProCard title={enumType} bordered collapsible classNames={{ root: 'i:mb-16' }}>
      <ResizableTable<EnumItemDTO>
        rowKey="enumValue"
        size="small"
        columns={COLUMNS}
        dataSource={items}
        search={false}
        options={false}
        toolBarRender={false}
        pagination={false}
      />
    </ProCard>
  );
};

export default EnumTable;
