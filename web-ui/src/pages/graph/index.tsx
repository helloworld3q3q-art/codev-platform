// 图谱可视化 —— 接管 cross-link 的 force-graph 面 (POST /api/v1/graph/cross-link/graph)。
// 用 react-force-graph-3d 渲染 cross_layer overview 全图。数据转换下沉 components/utils.ts。
// X-Project-Id 由 fetch 拦截器全局注入，本页只调生成 service。
import { useCallback, useState } from 'react';
import { ProCard } from '@ant-design/pro-components';
import { Button, message } from 'antd';
import ForceGraph3D from 'react-force-graph-3d';

import PageContainer from '@/components/PageContainer';
import { postGraph2 } from '@/services/apis/graphapi';

import { convertGraphData, type ForceGraphData, type GraphApiData } from './components/utils';

const EMPTY_GRAPH: ForceGraphData = { nodes: [], links: [] };

export default function GraphPage() {
  const [graph, setGraph] = useState<ForceGraphData>(EMPTY_GRAPH);

  const handleLoad = useCallback(async (): Promise<void> => {
    try {
      const res = await postGraph2({ mode: 'overview' });
      setGraph(convertGraphData(res.data as GraphApiData | undefined));
    } catch {
      message.error('图谱加载失败（确认后端可用 + 项目已建 cross_link 索引）');
    }
  }, []);

  return (
    <PageContainer>
      <ProCard
        title="跨层链路图 (cross-link)"
        bordered
        extra={
          <Button type="primary" onClick={handleLoad}>
            加载全图
          </Button>
        }
      >
        <div className="w-full" style={{ height: 600 }}>
          <ForceGraph3D graphData={graph} nodeAutoColorBy="kind" nodeLabel="name" />
        </div>
      </ProCard>
    </PageContainer>
  );
}
