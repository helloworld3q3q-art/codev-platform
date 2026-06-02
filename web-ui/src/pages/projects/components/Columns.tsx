import type { ProColumns } from '@ant-design/pro-components';
import { Tag, Typography } from 'antd';

import type { ProjectRow } from './utils';

interface CreateColumnsProps {
  context: {
    statusMap: Record<string, string>;
  };
  onLoad: (record: ProjectRow) => void;
  onUnload: (record: ProjectRow) => void;
}

// 列定义集中维护，index.tsx 只负责状态和回调编排
export function createColumns({
  context,
  onLoad,
  onUnload,
}: CreateColumnsProps): ProColumns<ProjectRow>[] {
  const { statusMap } = context;

  return [
    { title: '项目编码', dataIndex: 'code', width: 180, copyable: true },
    { title: '名称', dataIndex: 'name', width: 200, search: false },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      search: false,
      render: (_, record) => <Tag>{statusMap[record.status ?? ''] ?? record.status ?? '-'}</Tag>,
    },
    {
      title: '运行态',
      dataIndex: 'loaded',
      width: 100,
      search: false,
      render: (_, record) => (
        <Tag color={record.loaded ? 'green' : 'default'}>{record.loaded ? '已加载' : '未加载'}</Tag>
      ),
    },
    { title: '仓路径', dataIndex: 'repoPath', ellipsis: true, search: false },
    {
      title: '操作',
      valueType: 'option',
      width: 100,
      fixed: 'right',
      render: (_, record) =>
        record.loaded
          ? [
              <Typography.Link key="unload" onClick={() => onUnload(record)}>
                卸载
              </Typography.Link>,
            ]
          : [
              <Typography.Link key="load" onClick={() => onLoad(record)}>
                加载
              </Typography.Link>,
            ],
    },
  ];
}
