// 影响分析结果面板: 命中则渲染 目标节点头 (kind/层/风险徽章) + summary + 按层清单;
// 未命中则渲染告警 + 同名歧义候选 (列出 id 让用户改用 id 精确查)。

import { Alert, Card, Tag, Typography } from 'antd';
import React from 'react';

import LayerGroups from './LayerGroups';
import {
  cleanSummary,
  LAYER_COLOR,
  LAYER_LABEL,
  RISK_COLOR,
  RISK_LABEL,
} from '../utils';
import type { ImpactNodeItem, ResultView } from '../utils';

const { Text } = Typography;

interface AmbiguousListProps {
  candidates: ImpactNodeItem[];
}

const AmbiguousList: React.FC<AmbiguousListProps> = ({ candidates }) => {
  return (
    <div className="mt-8 flex flex-col gap-4">
      {candidates.map((c) => (
        <div key={c.id} className="text-12 flex items-center gap-8 flex-wrap">
          <Text code copyable>
            {c.id}
          </Text>
          {c.kind ? <Tag>{c.kind}</Tag> : null}
          {c.file ? (
            <span className="text-#8c8c8c break-all">
              {c.file}
              {c.line ? `:${c.line}` : ''}
            </span>
          ) : null}
        </div>
      ))}
    </div>
  );
};

interface ResultPanelProps {
  result: ResultView;
}

const ResultPanel: React.FC<ResultPanelProps> = ({ result }) => {
  if (!result.found) {
    const hasAmbig = result.ambiguous.length > 0;
    return (
      <Card>
        <Alert
          type={hasAmbig ? 'warning' : 'info'}
          showIcon
          title={hasAmbig ? '名称有多个同名候选' : '未找到结果'}
          description={
            <div>
              <div>{result.emptyHint}</div>
              {hasAmbig ? <AmbiguousList candidates={result.ambiguous} /> : null}
            </div>
          }
        />
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex items-center gap-8 flex-wrap mb-12">
        <span className="text-16 font-600">{result.targetName || '—'}</span>
        {result.targetKind ? <Tag>{result.targetKind}</Tag> : null}
        {result.targetLayer ? (
          <Tag color={LAYER_COLOR[result.targetLayer] ?? 'default'}>
            {LAYER_LABEL[result.targetLayer] ?? result.targetLayer}
          </Tag>
        ) : null}
        {result.risk ? (
          <Tag color={RISK_COLOR[result.risk] ?? 'default'}>
            {RISK_LABEL[result.risk] ?? result.risk}
          </Tag>
        ) : null}
        <span className="text-12 text-#8c8c8c">共 {result.total} 个关联节点</span>
      </div>

      {result.summary ? (
        <div className="mb-16 p-12 bg-#f5f5f5 rounded-6 whitespace-pre-wrap break-all text-13">
          {cleanSummary(result.summary)}
        </div>
      ) : null}

      <LayerGroups byLayer={result.byLayer} />
    </Card>
  );
};

export default ResultPanel;
