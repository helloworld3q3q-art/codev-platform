// 统一图谱 kind 多选筛选浮层 — 勾选要显示的节点类型, 附每类颜色圆点 + 计数。
// 受控组件: selected 由父级管理, 通过 onChange 回传。

import { Checkbox } from 'antd';
import React, { useCallback } from 'react';

import { unifiedKindLabelOf, unifiedNodeColorOf } from '../common/utils';

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

interface KindFilterProps {
  // 全部出现过的 kind → 计数 (来自 stats.nodesByKind)
  kindCounts: Record<string, number>;
  // 当前选中的 kind 集合
  selected: string[];
  onToggle: (kind: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
}

const KindFilter: React.FC<KindFilterProps> = ({
  kindCounts,
  selected,
  onToggle,
  onSelectAll,
  onClear,
}) => {
  const kinds = Object.keys(kindCounts).sort();
  const selectedSet = new Set(selected);

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
        <span className="text-12 font-600">节点类型</span>
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
      <div className="flex flex-col gap-6">
        {kinds.map((k) => (
          <KindFilterItem
            key={k}
            kind={k}
            count={kindCounts[k] ?? 0}
            checked={selectedSet.has(k)}
            onToggle={onToggle}
          />
        ))}
        {kinds.length === 0 ? (
          <span className="text-12 text-#8c8c8c">暂无数据</span>
        ) : null}
      </div>
    </div>
  );
};

export default KindFilter;
