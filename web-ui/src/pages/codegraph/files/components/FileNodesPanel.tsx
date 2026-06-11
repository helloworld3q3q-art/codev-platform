// 右侧详情面板：选中文件后展示元信息卡片 + 通过 'contains' 边拿到的内部节点列表。

import { ApartmentOutlined, FileTextOutlined } from '@ant-design/icons';
import { history } from '@umijs/max';
import { Alert, Card, Descriptions, Empty, List, Skeleton, Space, Tag, Typography } from 'antd';
import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { postCodegraphNode, postNeighbors } from '@/services/apis/graphapi';

import type { FileDTO, NodeDTO } from '@/pages/codegraph/common/types';
import { langColorOf, nodeColorOf } from '@/pages/codegraph/common/utils';

import { formatBytes } from '../utils';

interface FileNodesPanelProps {
  filePath: string | undefined;
  // 兜底：左侧已经查过的文件元信息，用于即使节点查询失败也能显示
  fileFallback: FileDTO | undefined;
}

interface PanelState {
  fileNode: NodeDTO | undefined;
  containedNodes: NodeDTO[];
  loadFailed: boolean;
}

const INITIAL_STATE: PanelState = {
  fileNode: undefined,
  containedNodes: [],
  loadFailed: false,
};

// 节点按 kind 分组排序的优先级（直观可读顺序）
const KIND_ORDER: string[] = [
  'class',
  'interface',
  'enum',
  'function',
  'method',
  'route',
  'field',
  'variable',
  'constant',
  'type_alias',
  'enum_member',
  'import',
];

const sortNodes = (nodes: NodeDTO[]): NodeDTO[] => {
  const indexOf = (kind?: string): number => {
    if (!kind) {
      return KIND_ORDER.length + 1;
    }
    const i = KIND_ORDER.indexOf(kind);
    return i === -1 ? KIND_ORDER.length : i;
  };
  return [...nodes].sort((a, b) => {
    const ka = indexOf(a.kind as string | undefined);
    const kb = indexOf(b.kind as string | undefined);
    if (ka !== kb) {
      return ka - kb;
    }
    const la = (a.startLine as number | undefined) ?? Number.MAX_SAFE_INTEGER;
    const lb = (b.startLine as number | undefined) ?? Number.MAX_SAFE_INTEGER;
    if (la !== lb) {
      return la - lb;
    }
    return ((a.name as string | undefined) ?? '').localeCompare((b.name as string | undefined) ?? '');
  });
};

interface NodeRowProps {
  node: NodeDTO;
  onClick: (id: string | undefined) => void;
}

const NodeRow: React.FC<NodeRowProps> = ({ node, onClick }) => {
  const handleClick = useCallback((): void => {
    onClick(node.id as string | undefined);
  }, [node.id, onClick]);

  const kind = node.kind as string | undefined;
  const startLine = node.startLine as number | undefined;
  const endLine = node.endLine as number | undefined;
  const name = node.name as string | undefined;
  const qualifiedName = node.qualifiedName as string | undefined;
  const kindColor = nodeColorOf(kind);

  return (
    <List.Item className="i:cursor-pointer hover:bg-#f5f5f5" onClick={handleClick}>
      <Space size={8} className="w-full">
        <Tag style={{ color: kindColor, borderColor: kindColor }}>{kind ?? 'unknown'}</Tag>
        <Typography.Text strong>{name ?? '(unnamed)'}</Typography.Text>
        {startLine !== undefined && startLine !== null ? (
          <Typography.Text type="secondary" className="text-12">
            L{startLine}
            {endLine !== undefined && endLine !== null && endLine !== startLine ? `-${endLine}` : ''}
          </Typography.Text>
        ) : null}
        {qualifiedName && qualifiedName !== name ? (
          <Typography.Text type="secondary" className="text-12 truncate">
            {qualifiedName}
          </Typography.Text>
        ) : null}
      </Space>
    </List.Item>
  );
};

const FileNodesPanel: React.FC<FileNodesPanelProps> = ({ filePath, fileFallback }) => {
  const [state, setState] = useState<PanelState>(INITIAL_STATE);
  const [loading, setLoading] = useState(false);

  const loadDetail = useCallback(async (): Promise<void> => {
    if (!filePath) {
      setState(INITIAL_STATE);
      return;
    }
    setLoading(true);
    const nodeId = `file:${filePath}`;
    try {
      // 并行：1) 拿文件节点元信息  2) 拿该文件 contains 出去的内部节点
      const [fileNode, neighbors] = await Promise.all([
        postCodegraphNode({ id: nodeId })
          .then((r) => r.data)
          .catch(() => undefined),
        postNeighbors({
          id: nodeId,
          direction: 'out',
          edgeKinds: ['contains'],
          depth: 1,
        })
          .then((r) => r.data)
          .catch(() => undefined),
      ]);
      const allNodes = neighbors?.nodes ?? [];
      // 排除中心节点本身（API 行为不确定，保险过滤一次）
      const contained = allNodes.filter((n: NodeDTO) => n.id !== nodeId);
      const loadFailed = !fileNode && !neighbors;
      setState({ fileNode, containedNodes: contained, loadFailed });
    } catch {
      setState({ ...INITIAL_STATE, loadFailed: true });
    } finally {
      setLoading(false);
    }
  }, [filePath]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  const groupedNodes = useMemo(() => sortNodes(state.containedNodes), [state.containedNodes]);

  const handleNodeClick = useCallback((nodeId: string | undefined): void => {
    if (!nodeId) {
      return;
    }
    // 跳到 graph 页面并附带 focus query param（即使 graph 还没接也无害）
    history.push(`/codegraph/graph?focus=${encodeURIComponent(nodeId)}`);
  }, []);

  const renderNodeItem = useCallback(
    (node: NodeDTO): React.ReactNode => <NodeRow node={node} onClick={handleNodeClick} />,
    [handleNodeClick],
  );

  if (!filePath) {
    return (
      <Card classNames={{ root: 'i:h-full' }}>
        <Empty description="从左侧选择一个文件查看节点详情" />
      </Card>
    );
  }

  const displayLanguage =
    (state.fileNode?.language as string | undefined) ?? (fileFallback?.language as string | undefined);
  const displaySize = fileFallback?.size as number | undefined;
  const displayNodeCount = fileFallback?.nodeCount as number | undefined;

  return (
    <Card
      classNames={{ root: 'i:h-full' }}
      title={
        <Space>
          <FileTextOutlined />
          <Typography.Text strong>{filePath}</Typography.Text>
        </Space>
      }
      extra={
        displayLanguage ? (
          <Tag style={{ color: langColorOf(displayLanguage), borderColor: langColorOf(displayLanguage) }}>
            {displayLanguage}
          </Tag>
        ) : null
      }
    >
      <Descriptions size="small" column={2} className="i:mb-16">
        <Descriptions.Item label="路径">{filePath}</Descriptions.Item>
        <Descriptions.Item label="语言">{displayLanguage ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="大小">{formatBytes(displaySize)}</Descriptions.Item>
        <Descriptions.Item label="节点数">{displayNodeCount ?? '-'}</Descriptions.Item>
      </Descriptions>

      {state.loadFailed ? (
        <Alert
          type="warning"
          title="无法加载文件节点详情"
          description="codegraph 服务暂未返回该文件的 contains 邻居。请确认后端已启动并已索引该文件。"
          showIcon
          className="i:mb-16"
        />
      ) : null}

      <Typography.Title level={5} className="i:mt-0 i:mb-12">
        <ApartmentOutlined className="i:mr-8" />
        内部节点 ({groupedNodes.length})
      </Typography.Title>

      {loading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : groupedNodes.length === 0 ? (
        <Empty
          description={
            state.loadFailed ? '加载失败' : '该文件未索引到 contains 边内部节点（可能仅为容器文件）'
          }
        />
      ) : (
        <List size="small" dataSource={groupedNodes} renderItem={renderNodeItem} />
      )}
    </Card>
  );
};

export default FileNodesPanel;
