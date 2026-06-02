// CodeGraph 表格列定义。
// 操作列只放 "查看"（跳到 graph 页以此节点为中心展开）。

import { history } from '@umijs/max';
import React from 'react';

import type { ProColumns } from '@ant-design/pro-components';
import { Tag, Tooltip, Typography } from 'antd';

import type { NodeDTO } from '../../common/types';
import { langColorOf, nodeColorOf } from '../../common/utils';

function handleViewInGraph(record: NodeDTO): void {
  if (!record.id) return;
  // 用 query 把 nodeId 带过去，graph 页可自动展开（后续可扩展）
  history.push(`/codegraph/graph?nodeId=${encodeURIComponent(record.id)}`);
}

const ViewLink: React.FC<{ record: NodeDTO }> = ({ record }) => {
  const onClick = React.useCallback((): void => {
    handleViewInGraph(record);
  }, [record]);
  return (
    <Typography.Link onClick={onClick} disabled={!record.id}>
      在图中查看
    </Typography.Link>
  );
};

export function createColumns(): ProColumns<NodeDTO>[] {
  return [
    {
      title: 'ID',
      dataIndex: 'id',
      width: 140,
      ellipsis: true,
      search: false,
      render: (_, record) => {
        const id = record.id ?? '';
        const short = id.length > 16 ? `${id.slice(0, 16)}...` : id;
        return (
          <Tooltip title={id}>
            <Typography.Text className="text-12" copyable={{ text: id }}>
              {short}
            </Typography.Text>
          </Tooltip>
        );
      },
    },
    {
      title: '类型',
      dataIndex: 'kind',
      width: 110,
      search: false,
      render: (_, record) => (
        <Tag color={nodeColorOf(record.kind)} className="i:m-0">
          {record.kind ?? '-'}
        </Tag>
      ),
    },
    {
      title: '名称',
      dataIndex: 'name',
      width: 200,
      ellipsis: true,
    },
    {
      title: '限定名',
      dataIndex: 'qualifiedName',
      width: 260,
      ellipsis: true,
      search: false,
      render: (_, record) => (
        <Tooltip title={record.qualifiedName ?? ''}>
          <Typography.Text className="text-12">{record.qualifiedName ?? '-'}</Typography.Text>
        </Tooltip>
      ),
    },
    {
      title: '语言',
      dataIndex: 'language',
      width: 110,
      search: false,
      render: (_, record) => {
        const lang = record.language ?? '-';
        return (
          <Tag color={langColorOf(lang)} className="i:m-0">
            {lang}
          </Tag>
        );
      },
    },
    {
      title: '文件路径',
      dataIndex: 'filePath',
      width: 320,
      ellipsis: true,
      search: false,
      render: (_, record) => {
        const path = record.filePath ?? '-';
        return (
          <Tooltip title={path}>
            <Typography.Text
              className="text-12"
              copyable={record.filePath ? { text: path } : false}
            >
              {path}
            </Typography.Text>
          </Tooltip>
        );
      },
    },
    {
      title: '起始行',
      dataIndex: 'startLine',
      width: 90,
      search: false,
      align: 'right',
    },
    {
      title: '结束行',
      dataIndex: 'endLine',
      width: 90,
      search: false,
      align: 'right',
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      fixed: 'right',
      search: false,
      render: (_, record) => <ViewLink record={record} />,
    },
  ];
}
