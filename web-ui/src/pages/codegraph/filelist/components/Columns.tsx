// CodeGraph 文件列表表格列定义。
// 操作列：在 graph 中查看（按 id=file:<path> 跳转）；切换 files 树视图查看节点。

import type { ProColumns } from '@ant-design/pro-components';
import { history } from '@umijs/max';
import { Tag, Tooltip, Typography } from 'antd';
import React from 'react';

import type { FileDTO } from '../../common/types';
import { langColorOf } from '../../common/utils';

function handleViewInGraph(record: FileDTO): void {
  if (!record.path) return;
  const id = `file:${record.path}`;
  history.push(`/codegraph/graph?nodeId=${encodeURIComponent(id)}`);
}

function handleViewInTree(record: FileDTO): void {
  if (!record.path) return;
  history.push(`/codegraph/files?path=${encodeURIComponent(record.path)}`);
}

const ViewGraphLink: React.FC<{ record: FileDTO }> = ({ record }) => {
  const onClick = React.useCallback((): void => {
    handleViewInGraph(record);
  }, [record]);
  return (
    <Typography.Link onClick={onClick} disabled={!record.path}>
      在图中查看
    </Typography.Link>
  );
};

const ViewTreeLink: React.FC<{ record: FileDTO }> = ({ record }) => {
  const onClick = React.useCallback((): void => {
    handleViewInTree(record);
  }, [record]);
  return (
    <Typography.Link onClick={onClick} disabled={!record.path}>
      文件树
    </Typography.Link>
  );
};

function formatBytes(bytes?: number): string {
  if (bytes === undefined || bytes === null) return '-';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export function createColumns(): ProColumns<FileDTO>[] {
  return [
    {
      title: '路径',
      dataIndex: 'path',
      width: 460,
      ellipsis: true,
      render: (_, record) => {
        const path = record.path ?? '-';
        return (
          <Tooltip title={path}>
            <Typography.Text className="text-12" copyable={record.path ? { text: path } : false}>
              {path}
            </Typography.Text>
          </Tooltip>
        );
      },
    },
    {
      title: '语言',
      dataIndex: 'language',
      width: 110,
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
      title: '节点数',
      dataIndex: 'nodeCount',
      width: 100,
      align: 'right',
      sorter: (a, b) => (a.nodeCount ?? 0) - (b.nodeCount ?? 0),
    },
    {
      title: '大小',
      dataIndex: 'size',
      width: 110,
      align: 'right',
      sorter: (a, b) => (a.size ?? 0) - (b.size ?? 0),
      render: (_, record) => formatBytes(record.size),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      fixed: 'right',
      render: (_, record) => (
        <div className="grid grid-cols-2 gap-x-8">
          <ViewGraphLink record={record} />
          <ViewTreeLink record={record} />
        </div>
      ),
    },
  ];
}
