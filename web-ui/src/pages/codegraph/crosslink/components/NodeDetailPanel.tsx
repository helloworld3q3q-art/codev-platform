// 点击 3D 节点时弹出的右侧详情浮层：节点元信息 + 文件位置 + 按 edge.kind 分组的邻居清单。
// 点击邻居行 → 通过 onJumpTo(neighborId) 切换中心节点（父组件同步 centerId 让 Graph3DCanvas 聚焦）。

import { ArrowRightOutlined, CloseOutlined, FileTextOutlined } from '@ant-design/icons';
import { Empty, Tag } from 'antd';
import React, { useCallback, useMemo } from 'react';

import type { EdgeDTO, NodeDTO } from '../../common/types';
import { nodeColorOf } from '../../common/utils';

export interface NodeDetailPanelProps {
  nodeId: string;
  nodes: NodeDTO[];
  edges: EdgeDTO[];
  onJumpTo: (id: string) => void;
  onClose: () => void;
}

interface NeighborRow {
  key: string;
  id: string;
  name: string;
  kind: string;
  filePath?: string;
  rel: string;
  direction: 'out' | 'in';
}

const NeighborItem: React.FC<{
  row: NeighborRow;
  onJump: (id: string) => void;
}> = ({ row, onJump }) => {
  const handleClick = useCallback((): void => {
    onJump(row.id);
  }, [onJump, row.id]);

  return (
    <button
      type="button"
      onClick={handleClick}
      className="w-full text-left cursor-pointer border-none bg-transparent px-8 py-6 rounded-4 hover:bg-#f5f5f5 flex items-start gap-8"
      style={{ transition: 'background 0.15s' }}
    >
      <span
        className="inline-block rounded-full flex-shrink-0"
        style={{
          width: 8,
          height: 8,
          marginTop: 6,
          background: nodeColorOf(row.kind),
        }}
      />
      <div className="flex-1 min-w-0">
        <div className="text-13 truncate" style={{ color: '#262626' }}>
          {row.name}
        </div>
        {row.filePath ? (
          <div className="text-11 text-#8c8c8c truncate">
            <FileTextOutlined className="mr-4" />
            {row.filePath}
          </div>
        ) : null}
      </div>
      <ArrowRightOutlined className="text-12 text-#bfbfbf mt-4" />
    </button>
  );
};

const NodeDetailPanel: React.FC<NodeDetailPanelProps> = ({
  nodeId,
  nodes,
  edges,
  onJumpTo,
  onClose,
}) => {
  const node = useMemo(() => nodes.find((n) => n.id === nodeId), [nodes, nodeId]);

  const nodeMap = useMemo(() => {
    const m = new Map<string, NodeDTO>();
    for (const n of nodes) {
      if (n.id) m.set(n.id, n);
    }
    return m;
  }, [nodes]);

  // 按 edge.kind 分组的邻居（含方向：out 自该节点指出，in 指向该节点）
  const neighborGroups = useMemo(() => {
    if (!nodeId) return [] as Array<{ rel: string; rows: NeighborRow[] }>;
    const groups = new Map<string, NeighborRow[]>();
    for (const e of edges) {
      if (e.source === nodeId && e.target) {
        const tgt = nodeMap.get(e.target);
        if (!tgt) continue;
        const rel = e.kind ?? 'unknown';
        const arr = groups.get(rel) ?? [];
        arr.push({
          key: `out-${rel}-${e.target}`,
          id: e.target,
          name: tgt.name ?? e.target,
          kind: tgt.kind ?? '',
          filePath: tgt.filePath,
          rel,
          direction: 'out',
        });
        groups.set(rel, arr);
      } else if (e.target === nodeId && e.source) {
        const src = nodeMap.get(e.source);
        if (!src) continue;
        const rel = `${e.kind ?? 'unknown'} (被引用)`;
        const arr = groups.get(rel) ?? [];
        arr.push({
          key: `in-${rel}-${e.source}`,
          id: e.source,
          name: src.name ?? e.source,
          kind: src.kind ?? '',
          filePath: src.filePath,
          rel,
          direction: 'in',
        });
        groups.set(rel, arr);
      }
    }
    return Array.from(groups.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([rel, rows]) => ({ rel, rows }));
  }, [edges, nodeId, nodeMap]);

  const totalNeighbors = useMemo(
    () => neighborGroups.reduce((s, g) => s + g.rows.length, 0),
    [neighborGroups],
  );

  if (!node) {
    return (
      <div
        className="absolute top-16 right-16 z-20 bg-white rounded-8 p-16 flex items-center justify-center"
        style={{ width: 360, boxShadow: '0 6px 24px rgba(0,0,0,0.12)' }}
      >
        <Empty description={`节点 ${nodeId} 不存在`} />
      </div>
    );
  }

  return (
    <div
      className="absolute top-16 right-16 z-20 bg-white rounded-8 flex flex-col"
      style={{
        width: 380,
        maxHeight: 'calc(100vh - 120px)',
        boxShadow: '0 6px 24px rgba(0,0,0,0.12)',
        border: '1px solid #f0f0f0',
      }}
    >
      {/* 头部：节点名 + 关闭按钮 */}
      <div
        className="flex items-start gap-8 px-16 py-12"
        style={{ borderBottom: '1px solid #f0f0f0' }}
      >
        <span
          className="inline-block rounded-full flex-shrink-0"
          style={{
            width: 12,
            height: 12,
            marginTop: 6,
            background: nodeColorOf(node.kind),
          }}
        />
        <div className="flex-1 min-w-0">
          <div className="text-14 font-600 break-all" style={{ color: '#262626' }}>
            {node.name}
          </div>
          <div className="text-12 text-#8c8c8c mt-2 flex items-center gap-6 flex-wrap">
            <Tag color={nodeColorOf(node.kind)} className="i:m-0">
              {node.kind}
            </Tag>
            {node.language ? <span>{node.language}</span> : null}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          title="关闭"
          className="cursor-pointer border-none bg-transparent text-#8c8c8c"
          style={{ padding: 4, lineHeight: 1, fontSize: 14 }}
        >
          <CloseOutlined />
        </button>
      </div>

      {/* 文件位置 */}
      {node.filePath ? (
        <div
          className="px-16 py-8 text-12 text-#595959 flex items-center gap-6"
          style={{ borderBottom: '1px solid #f0f0f0', background: '#fafafa' }}
        >
          <FileTextOutlined />
          <span className="break-all">
            {node.filePath}
            {node.startLine ? `:${node.startLine}` : ''}
          </span>
        </div>
      ) : null}

      {/* 邻居清单 */}
      <div className="flex-1 overflow-auto px-8 py-8">
        <div className="px-8 pb-8 text-12 text-#8c8c8c">
          关联节点 <span className="font-600 text-#262626">{totalNeighbors}</span> 个 · 点击跳转
        </div>
        {neighborGroups.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无关联节点" />
        ) : (
          neighborGroups.map((g) => (
            <div key={g.rel} className="mb-12">
              <div className="px-8 py-4 text-11 font-600 uppercase text-#8c8c8c">
                {g.rel} · {g.rows.length}
              </div>
              {g.rows.map((row) => (
                <NeighborItem key={row.key} row={row} onJump={onJumpTo} />
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default NodeDetailPanel;
