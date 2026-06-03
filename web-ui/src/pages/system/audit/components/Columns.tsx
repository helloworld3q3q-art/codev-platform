import type { ProColumns } from '@ant-design/pro-components';
import { Tag } from 'antd';
import dayjs from 'dayjs';

import type { AuditRow } from '../types';

// 放行结果筛选项 (本地展示常量: true/false 二元, 仅驱动 UI select, 后端按字符串识别)。
const ALLOWED_OPTIONS = [
  { label: '放行', value: 'true' },
  { label: '拒绝', value: 'false' },
];

export function createColumns(): ProColumns<AuditRow>[] {
  return [
    {
      title: '时间',
      dataIndex: 'ts',
      width: 180,
      search: false,
      render: (_, record) => (record.ts ? dayjs(record.ts).format('YYYY/MM/DD HH:mm:ss') : '-'),
    },
    { title: '服务', dataIndex: 'service', width: 140 },
    { title: '用户', dataIndex: 'userId', width: 140 },
    { title: '组织', dataIndex: 'orgId', width: 140, search: false },
    { title: '项目', dataIndex: 'projectId', width: 160 },
    {
      title: '结果',
      dataIndex: 'allowed',
      width: 100,
      valueType: 'select',
      fieldProps: { options: ALLOWED_OPTIONS },
      render: (_, record) =>
        record.allowed ? <Tag color="success">放行</Tag> : <Tag color="error">拒绝</Tag>,
    },
    { title: '原因', dataIndex: 'reason', ellipsis: true, search: false },
    { title: '鉴权方式', dataIndex: 'via', width: 140, search: false },
    {
      title: '时间范围',
      dataIndex: 'tsRange',
      valueType: 'dateTimeRange',
      hideInTable: true,
    },
  ];
}
