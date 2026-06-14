import type { ProColumns } from '@ant-design/pro-components';
import { Badge, Tag, Typography } from 'antd';
import { useCallback } from 'react';

import type { TokenRow } from './utils';
import { formatExpiry, formatProjects, STATUS_BADGE } from './utils';

interface RevokeActionProps {
  record: TokenRow;
  onRevoke: (record: TokenRow) => void;
}

// jsx-no-bind: 操作列回调提取子组件 + useCallback。
const RevokeAction: React.FC<RevokeActionProps> = ({ record, onRevoke }) => {
  const handleClick = useCallback(() => onRevoke(record), [record, onRevoke]);
  return (
    <Typography.Link type="danger" onClick={handleClick}>
      吊销
    </Typography.Link>
  );
};

interface CreateColumnsContext {
  statusMap: Record<string, string>;
  onRevoke: (record: TokenRow) => void;
}

export function createColumns({
  context,
}: {
  context: CreateColumnsContext;
}): ProColumns<TokenRow>[] {
  const { statusMap, onRevoke } = context;

  return [
    { title: '用户', dataIndex: 'userId', width: 140, search: false },
    { title: '组织', dataIndex: 'orgId', width: 120, search: false },
    {
      title: '项目权限',
      dataIndex: 'projects',
      width: 180,
      ellipsis: true,
      search: false,
      render: (_, record) => formatProjects(record.projects),
    },
    { title: '备注', dataIndex: 'label', width: 160, ellipsis: true, search: false },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      search: false,
      render: (_, record) => {
        const status = record.status ?? '';
        return (
          <Badge status={STATUS_BADGE[status] ?? 'default'} text={statusMap[status] ?? status ?? '-'} />
        );
      },
    },
    {
      title: '到期',
      dataIndex: 'expiresAt',
      width: 150,
      search: false,
      render: (_, record) => formatExpiry(record.expiresAt),
    },
    {
      title: 'Hash 前缀',
      dataIndex: 'tokenHashPrefix',
      width: 140,
      search: false,
      render: (_, record) =>
        record.tokenHashPrefix ? <Tag>{record.tokenHashPrefix}</Tag> : '-',
    },
    {
      title: '操作',
      valueType: 'option',
      width: 90,
      fixed: 'right',
      render: (_, record) =>
        record.status === 'ACTIVE' ? <RevokeAction record={record} onRevoke={onRevoke} /> : '-',
    },
  ];
}
