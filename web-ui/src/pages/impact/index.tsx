// 影响分析 — README 核心卖点 "改一处 → 跨层影响清单" 的前端入口。
// 吃 A1 桥接后连通的统一图谱 store (reports API), 4 种查询: 改动影响 / 表被谁用 / 页依赖 / 端点被谁调。
// 直接调生成的 reportsapi (postImpact / postTableUsage / postPageDependencies / postApiCallers), 自取 .data。

import { useModel } from '@umijs/max';
import { Card, Empty, Input, Radio, Spin, message } from 'antd';
import type { RadioChangeEvent } from 'antd';
import React, { useCallback, useEffect, useState } from 'react';

import {
  postApiCallers,
  postImpact,
  postPageDependencies,
  postTableUsage,
} from '@/services/apis/reportsapi';
import { postImpactPaths } from '@/services/apis/graphapi';

import ResultPanel from './components/ResultPanel';
import {
  normalizeImpact,
  normalizeImpactPaths,
  normalizeQuery,
  QUERY_META,
  QUERY_OPTIONS,
} from './utils';
import type { QueryKind, ResultView } from './utils';

const ImpactPage: React.FC = () => {
  // 订阅当前项目, 切项目后清空结果 (fetch 拦截器据此注入 X-Project-Id, 重查走最新项目)。
  const { currentProjectId } = useModel('project');
  const [queryKind, setQueryKind] = useState<QueryKind>('impact');
  const [keyword, setKeyword] = useState('');
  const [result, setResult] = useState<ResultView | undefined>(undefined);
  const [loading, setLoading] = useState(false);

  const runQuery = useCallback(async (kind: QueryKind, value: string): Promise<void> => {
    const ref = value.trim();
    if (!ref) {
      message.warning('请输入查询目标');
      return;
    }
    setLoading(true);
    try {
      if (kind === 'impact') {
        const res = await postImpact({ nodeRef: ref });
        setResult(normalizeImpact(res.data));
      } else if (kind === 'tableUsage') {
        const res = await postTableUsage({ table: ref });
        setResult(normalizeQuery(kind, res.data));
      } else if (kind === 'pageDependencies') {
        const res = await postPageDependencies({ pageRef: ref });
        setResult(normalizeQuery(kind, res.data));
      } else if (kind === 'apiCallers') {
        const res = await postApiCallers({ endpointRef: ref });
        setResult(normalizeQuery(kind, res.data));
      } else {
        const res = await postImpactPaths({ nodeRef: ref });
        setResult(normalizeImpactPaths(res.data));
      }
    } catch {
      setResult(undefined);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSearch = useCallback(
    (value: string): void => {
      runQuery(queryKind, value);
    },
    [queryKind, runQuery],
  );

  const handleKindChange = useCallback((e: RadioChangeEvent): void => {
    setQueryKind(e.target.value);
    setResult(undefined);
    setKeyword('');
  }, []);

  const handleKeywordChange = useCallback((e: React.ChangeEvent<HTMLInputElement>): void => {
    setKeyword(e.target.value);
  }, []);

  useEffect(() => {
    setResult(undefined);
  }, [currentProjectId]);

  return (
    <div className="p-16">
      <Card classNames={{ root: 'i:mb-16' }}>
        <div className="mb-12">
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            options={QUERY_OPTIONS}
            value={queryKind}
            onChange={handleKindChange}
          />
        </div>
        <Input.Search
          placeholder={QUERY_META[queryKind].placeholder}
          enterButton="分析"
          allowClear
          value={keyword}
          loading={loading}
          onChange={handleKeywordChange}
          onSearch={handleSearch}
        />
        <div className="mt-8 text-12 text-#8c8c8c">{QUERY_META[queryKind].hint}</div>
      </Card>

      <Spin spinning={loading}>
        {result ? (
          <ResultPanel result={result} />
        ) : (
          <Card>
            <Empty description="输入目标后点击「分析」查看跨层影响" />
          </Card>
        )}
      </Spin>
    </div>
  );
};

export default ImpactPage;
