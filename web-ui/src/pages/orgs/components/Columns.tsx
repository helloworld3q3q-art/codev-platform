import type { ProColumns } from '@ant-design/pro-components';
import { Tag, Typography } from 'antd';

import { ORG_STATUS_ACTIVE, type OrgRow } from './utils';

interface CreateColumnsProps {
  context: {
    statusMap: Record<string, string>;
  };
  onEdit: (record: OrgRow) => void;
  onToggleStatus: (record: OrgRow) => void;
  onMembers: (record: OrgRow) => void;
}

// 列定义集中维护，index.tsx 只负责状态和回调编排。
// Columns.tsx 豁免 jsx-no-bind, 列内联箭头合法。
export function createColumns({
  context,
  onEdit,
  onToggleStatus,
  onMembers,
}: CreateColumnsProps): ProColumns<OrgRow>[] {
  const { statusMap } = context;

  return [
    { title: '组织编码', dataIndex: 'code', width: 180, copyable: true },
    { title: '组织名称', dataIndex: 'name', width: 200, search: false },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      search: false,
      render: (_, record) => {
        const isActive = record.status === ORG_STATUS_ACTIVE;
        return (
          <Tag color={isActive ? 'green' : 'default'}>
            {statusMap[record.status ?? ''] ?? record.status ?? '-'}
          </Tag>
        );
      },
    },
    { title: '描述', dataIndex: 'description', ellipsis: true, search: false },
    {
      title: '操作',
      valueType: 'option',
      width: 180,
      fixed: 'right',
      render: (_, record) => {
        const isActive = record.status === ORG_STATUS_ACTIVE;
        return [
          <Typography.Link key="edit" onClick={() => onEdit(record)}>
            编辑
          </Typography.Link>,
          <Typography.Link key="status" onClick={() => onToggleStatus(record)}>
            {isActive ? '禁用' : '启用'}
          </Typography.Link>,
          <Typography.Link key="members" onClick={() => onMembers(record)}>
            成员管理
          </Typography.Link>,
        ];
      },
    },
  ];
}
