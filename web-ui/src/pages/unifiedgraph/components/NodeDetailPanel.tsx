// 统一图谱 — 点击节点弹出的详情面板(右侧浮层)。展示节点 kind/名称/ID/文件/语言/meta。
import { CloseOutlined } from '@ant-design/icons';
import { Tag, Typography } from 'antd';
import React from 'react';

import type { UnifiedGraphNode } from '../common/types';
import { unifiedKindLabelOf, unifiedNodeColorOf } from '../common/utils';

interface NodeDetailPanelProps {
  node: UnifiedGraphNode;
  onClose: () => void;
}

const DetailRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => {
  return (
    <div className="flex gap-8 text-12 mb-8">
      <span className="text-#8c8c8c shrink-0 w-64">{label}</span>
      <span className="flex-1 break-all">{children}</span>
    </div>
  );
};

const NodeDetailPanel: React.FC<NodeDetailPanelProps> = ({ node, onClose }) => {
  const metaEntries = Object.entries(node.meta ?? {});
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
    </div>
  );
};

export default NodeDetailPanel;
