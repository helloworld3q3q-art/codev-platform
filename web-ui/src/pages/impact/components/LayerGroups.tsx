// 按层 (前端 / 后端 / 数据) 分组渲染受影响 / 关联节点清单。
// 节点行全用原生 DOM 元素 (div/span), 免 react/jsx-no-bind; Tag 仅展示无函数 props。

import { Empty, Tag } from 'antd';
import React from 'react';

import { LAYER_COLOR, LAYER_LABEL, LAYER_ORDER } from '../utils';
import type { ImpactNodeItem } from '../utils';

interface NodeRowProps {
  item: ImpactNodeItem;
}

const NodeRow: React.FC<NodeRowProps> = ({ item }) => {
  const loc = item.file ? `${item.file}${item.line ? `:${item.line}` : ''}` : '';
  return (
    <div className="px-12 py-6 rounded-6 bg-#fafafa flex items-center gap-8 flex-wrap">
      <span className="font-500">{item.name || item.id}</span>
      {item.kind ? <Tag>{item.kind}</Tag> : null}
      {item.viaEdge ? <span className="text-12 text-#8c8c8c">via {item.viaEdge}</span> : null}
      {item.depth ? <span className="text-12 text-#bfbfbf">深度 {item.depth}</span> : null}
      {loc ? <span className="text-12 text-#bfbfbf break-all">{loc}</span> : null}
    </div>
  );
};

interface LayerSectionProps {
  layer: string;
  items: ImpactNodeItem[];
}

const LayerSection: React.FC<LayerSectionProps> = ({ layer, items }) => {
  return (
    <div className="mb-16">
      <div className="mb-8 flex items-center gap-8">
        <Tag color={LAYER_COLOR[layer] ?? 'default'}>{LAYER_LABEL[layer] ?? layer}</Tag>
        <span className="text-12 text-#8c8c8c">{items.length} 个</span>
      </div>
      <div className="flex flex-col gap-4">
        {items.map((it) => (
          <NodeRow key={it.id || it.name} item={it} />
        ))}
      </div>
    </div>
  );
};

interface LayerGroupsProps {
  byLayer: Record<string, ImpactNodeItem[]>;
}

const LayerGroups: React.FC<LayerGroupsProps> = ({ byLayer }) => {
  const layers = LAYER_ORDER.filter((l) => (byLayer[l]?.length ?? 0) > 0);
  if (layers.length === 0) {
    return <Empty description="无下游 / 无关联节点 (孤立或叶子节点)" />;
  }
  return (
    <div>
      {layers.map((layer) => (
        <LayerSection key={layer} layer={layer} items={byLayer[layer]} />
      ))}
    </div>
  );
};

export default LayerGroups;
