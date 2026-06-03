// 统一图谱 — 点击节点弹出的详情面板(右侧浮层)。
// 上半: 节点 kind/名称/ID/文件/语言/meta。
// 下半: 按边类型分组的**关联节点**(出/入向), 点击跳转 —— 这就是原"跨层链路"页的
//       表引用 / 端点关联能力, 直接由统一 store 的边 (reads/writes_table / calls_api /
//       defines_column ...) 还原, 不再需要独立页面 + 独立查询接口。

import { ArrowRightOutlined, CloseOutlined } from '@ant-design/icons';
import { Tag, Typography } from 'antd';
import React, { useCallback, useMemo } from 'react';

import type { UnifiedGraphEdge, UnifiedGraphNode } from '../common/types';
import {
  unifiedEdgeLabelOf,
  unifiedKindLabelOf,
  unifiedNodeColorOf,
} from '../common/utils';

interface NodeDetailPanelProps {
  node: UnifiedGraphNode;
  nodes: UnifiedGraphNode[];
  edges: UnifiedGraphEdge[];
  onJumpTo: (id: string) => void;
  onClose: () => void;
}

interface NeighborRow {
  key: string;
  id: string;
  name: string;
  kind: string;
  group: string;
}

const _GROUP_CAP = 50;

const DetailRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => {
  return (
    <div className="flex gap-8 text-12 mb-8">
      <span className="text-#8c8c8c shrink-0 w-64">{label}</span>
      <span className="flex-1 break-all">{children}</span>
    </div>
  );
};

const NeighborItem: React.FC<{ row: NeighborRow; onJump: (id: string) => void }> = ({
  row,
  onJump,
}) => {
  const handleClick = useCallback((): void => {
    onJump(row.id);
  }, [onJump, row.id]);

  return (
    <button
      type="button"
      onClick={handleClick}
      className="w-full text-left cursor-pointer border-none bg-transparent px-6 py-4 rounded-4 hover:bg-#f5f5f5 flex items-center gap-6"
    >
      <span
        className="inline-block w-8 h-8 rounded-full shrink-0"
        style={{ background: unifiedNodeColorOf(row.kind) }}
      />
      <span className="flex-1 min-w-0 text-12 truncate" style={{ color: '#262626' }}>
        {row.name}
      </span>
      <ArrowRightOutlined className="text-11 text-#bfbfbf" />
    </button>
  );
};

const NodeDetailPanel: React.FC<NodeDetailPanelProps> = ({
  node,
  nodes,
  edges,
  onJumpTo,
  onClose,
}) => {
  const metaEntries = Object.entries(node.meta ?? {});

  const nodeById = useMemo(() => {
    const m = new Map<string, UnifiedGraphNode>();
    for (const n of nodes) {
      if (n.id) m.set(n.id, n);
    }
    return m;
  }, [nodes]);

  // 按 边类型+方向 分组的关联节点 (出向: 本节点 -> X; 入向: X -> 本节点)。
  const groups = useMemo(() => {
    const map = new Map<string, NeighborRow[]>();
    for (const e of edges) {
      let otherId: string | undefined;
      let group: string | undefined;
      if (e.source === node.id && e.target) {
        otherId = e.target;
        group = unifiedEdgeLabelOf(e.kind);
      } else if (e.target === node.id && e.source) {
        otherId = e.source;
        group = `${unifiedEdgeLabelOf(e.kind)} (被引用)`;
      }
      if (!otherId || !group) continue;
      const other = nodeById.get(otherId);
      if (!other) continue;
      const arr = map.get(group) ?? [];
      arr.push({
        key: `${group}-${otherId}`,
        id: otherId,
        name: other.name ?? otherId,
        kind: other.kind ?? '',
        group,
      });
      map.set(group, arr);
    }
    return Array.from(map.entries())
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([group, rows]) => ({ group, rows }));
  }, [edges, node.id, nodeById]);

  const total = groups.reduce((s, g) => s + g.rows.length, 0);

  return (
    <div
      className="absolute top-64 right-16 z-10 w-320 p-16 rounded-8"
      style={{
        background: 'rgba(255, 255, 255, 0.96)',
        backdropFilter: 'blur(4px)',
        border: '1px solid #f0f0f0',
        boxShadow: '0 4px 16px rgba(0, 0, 0, 0.12)',
        maxHeight: 'calc(100vh - 130px)',
        overflow: 'auto',
      }}
    >
      <div className="flex items-center justify-between mb-12">
        <Tag color={unifiedNodeColorOf(node.kind)} className="i:m-0">
          {unifiedKindLabelOf(node.kind)}
        </Tag>
        <CloseOutlined className="cursor-pointer text-#8c8c8c" onClick={onClose} />
      </div>
      <DetailRow label="名称">
        <Typography.Text strong copyable={node.name ? { text: node.name } : false}>
          {node.name ?? '-'}
        </Typography.Text>
      </DetailRow>
      <DetailRow label="ID">
        <Typography.Text className="text-12" copyable={node.id ? { text: node.id } : false}>
          {node.id ?? '-'}
        </Typography.Text>
      </DetailRow>
      {node.filePath ? (
        <DetailRow label="文件">
          {node.filePath}
          {node.startLine ? `:${node.startLine}` : ''}
        </DetailRow>
      ) : null}
      {node.language ? <DetailRow label="语言">{node.language}</DetailRow> : null}
      {metaEntries.length > 0 ? (
        <div className="mt-12 pt-8" style={{ borderTop: '1px solid #f0f0f0' }}>
          {metaEntries.map(([k, v]) => (
            <DetailRow key={k} label={k}>
              {String(v)}
            </DetailRow>
          ))}
        </div>
      ) : null}

      <div className="mt-12 pt-8" style={{ borderTop: '1px solid #f0f0f0' }}>
        <div className="text-12 text-#8c8c8c mb-6">
          关联节点 <span className="font-600 text-#262626">{total}</span> · 点击跳转
        </div>
        {groups.length === 0 ? (
          <div className="text-12 text-#bfbfbf">无关联</div>
        ) : (
          groups.map((g) => (
            <div key={g.group} className="mb-8">
              <div className="text-11 font-600 text-#8c8c8c mb-2">
                {g.group} · {g.rows.length}
              </div>
              {g.rows.slice(0, _GROUP_CAP).map((row) => (
                <NeighborItem key={row.key} row={row} onJump={onJumpTo} />
              ))}
              {g.rows.length > _GROUP_CAP ? (
                <div className="text-11 text-#bfbfbf pl-6">
                  …另 {g.rows.length - _GROUP_CAP} 个
                </div>
              ) : null}
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default NodeDetailPanel;
