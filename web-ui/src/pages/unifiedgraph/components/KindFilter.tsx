// 统一图谱 kind 多选筛选浮层 — 按层分组 (前端/后端/数据库/...), 每类带颜色圆点 + 计数。
// 每层一个"本层"切换 (整层全选/取消), 用于快速看某一层或拼"跨层链路"视图。
// 受控组件: selected 由父级管理, 通过 onToggle / onToggleLayer 回传。

import { Checkbox } from 'antd';
import React, { useCallback, useMemo } from 'react';

import {
  LAYER_ORDER,
  unifiedKindLabelOf,
  unifiedLayerOf,
  unifiedNodeColorOf,
} from '../common/utils';

interface KindFilterItemProps {
  kind: string;
  count: number;
  checked: boolean;
  onToggle: (kind: string) => void;
}

const KindFilterItem: React.FC<KindFilterItemProps> = ({ kind, count, checked, onToggle }) => {
  const handleChange = useCallback((): void => {
    onToggle(kind);
  }, [kind, onToggle]);

  return (
    <Checkbox checked={checked} onChange={handleChange} className="i:m-0 flex items-center">
      <span className="flex items-center gap-6">
        <span
          className="inline-block w-8 h-8 rounded-full"
          style={{ background: unifiedNodeColorOf(kind) }}
        />
        <span className="text-12">{unifiedKindLabelOf(kind)}</span>
        <span className="text-11 text-#8c8c8c">({count})</span>
      </span>
    </Checkbox>
  );
};

interface LayerGroupProps {
  layer: string;
  kinds: string[];
  kindCounts: Record<string, number>;
  selectedSet: Set<string>;
  onToggle: (kind: string) => void;
  onToggleLayer: (kinds: string[]) => void;
}

const LayerGroup: React.FC<LayerGroupProps> = ({
  layer,
  kinds,
  kindCounts,
  selectedSet,
  onToggle,
  onToggleLayer,
}) => {
  const handleLayer = useCallback((): void => {
    onToggleLayer(kinds);
  }, [kinds, onToggleLayer]);

  const total = kinds.reduce((s, k) => s + (kindCounts[k] ?? 0), 0);

  return (
    <div className="mb-10">
      <div className="flex items-center justify-between mb-4">
        <span className="text-11 font-600 text-#595959">
          {layer} <span className="text-#bfbfbf">({total})</span>
        </span>
        <button
          type="button"
          className="border-none bg-transparent text-#1677ff cursor-pointer text-11"
          onClick={handleLayer}
        >
          本层
        </button>
      </div>
      <div className="flex flex-col gap-4 pl-4">
        {kinds.map((k) => (
          <KindFilterItem
            key={k}
            kind={k}
            count={kindCounts[k] ?? 0}
            checked={selectedSet.has(k)}
            onToggle={onToggle}
          />
        ))}
      </div>
    </div>
  );
};

interface KindFilterProps {
  // 全部出现过的 kind → 计数 (来自 stats.nodesByKind)
  kindCounts: Record<string, number>;
  // 当前选中的 kind 集合
  selected: string[];
  onToggle: (kind: string) => void;
  onToggleLayer: (kinds: string[]) => void;
  onSelectAll: () => void;
  onClear: () => void;
}

const KindFilter: React.FC<KindFilterProps> = ({
  kindCounts,
  selected,
  onToggle,
  onToggleLayer,
  onSelectAll,
  onClear,
}) => {
  const selectedSet = new Set(selected);

  const groups = useMemo(() => {
    const byLayer = new Map<string, string[]>();
    for (const k of Object.keys(kindCounts)) {
      const layer = unifiedLayerOf(k);
      const arr = byLayer.get(layer) ?? [];
      arr.push(k);
      byLayer.set(layer, arr);
    }
    return LAYER_ORDER.filter((l) => byLayer.has(l)).map((l) => ({
      layer: l,
      kinds: (byLayer.get(l) ?? []).sort(),
    }));
  }, [kindCounts]);

  return (
    <div
      className="absolute top-16 left-16 z-10 px-12 py-10 rounded-6"
      style={{
        width: 220,
        maxHeight: 'calc(100vh - 140px)',
        overflowY: 'auto',
        background: 'rgba(255, 255, 255, 0.94)',
        backdropFilter: 'blur(4px)',
        border: '1px solid #f0f0f0',
        boxShadow: '0 2px 8px rgba(0, 0, 0, 0.06)',
      }}
    >
      <div className="flex items-center justify-between mb-8">
        <span className="text-12 font-600">节点分层</span>
        <span className="text-11">
          <button
            type="button"
            className="border-none bg-transparent text-#1677ff cursor-pointer text-11"
            onClick={onSelectAll}
          >
            全选
          </button>
          <span className="text-#d9d9d9 mx-4">|</span>
          <button
            type="button"
            className="border-none bg-transparent text-#1677ff cursor-pointer text-11"
            onClick={onClear}
          >
            清空
          </button>
        </span>
      </div>
      {groups.map((g) => (
        <LayerGroup
          key={g.layer}
          layer={g.layer}
          kinds={g.kinds}
          kindCounts={kindCounts}
          selectedSet={selectedSet}
          onToggle={onToggle}
          onToggleLayer={onToggleLayer}
        />
      ))}
      {groups.length === 0 ? (
        <span className="text-12 text-#8c8c8c">暂无数据</span>
      ) : null}
    </div>
  );
};

export default KindFilter;
