import type { ProColumns } from '@ant-design/pro-components';
import { Badge } from 'antd';

import type { MemoryRow } from '../types';
import { STATUS_BADGE } from '../types';

interface CreateColumnsContext {
  statusMap: Record<string, string>;
  kindMap: Record<string, string>;
}

export function createColumns({
  context,
}: {
  context: CreateColumnsContext;
}): ProColumns<MemoryRow>[] {
  const { statusMap, kindMap } = context;

  return [
    {
      title: '内容',
      dataIndex: 'content',
      width: 400,
      ellipsis: true,
      render: (_, record) => record.content ?? '-',
    },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 120,
      render: (_, record) => {
        const kind = (record.kind as string | undefined) ?? '';
        return kindMap[kind] ?? kind ?? '-';
      },
    },
    {
      title: '作用域 ref',
      dataIndex: 'scopeRef',
      width: 180,
      ellipsis: true,
      render: (_, record) => record.scopeRef ?? '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (_, record) => {
        const status = record.status ?? '';
        return (
          <Badge status={STATUS_BADGE[status] ?? 'default'} text={statusMap[status] ?? status ?? '-'} />
        );
      },
    },
  ];
}
