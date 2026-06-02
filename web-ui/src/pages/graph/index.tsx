// 图谱可视化 —— 接管 cross-link 的 force-graph 面 (POST /api/v1/graph/cross-link/graph)。
// 用 react-force-graph-3d 渲染 cross_layer overview 全图。数据转换下沉 components/utils.ts。
// TODO: post 不支持自定义 header，X-Project-Id 暂用 customRequest 透传；接全局项目选择后改读上下文。
import { useCallback, useState } from 'react';
import { ProCard } from '@ant-design/pro-components';
import { Button, message } from 'antd';
import ForceGraph3D from 'react-force-graph-3d';

import PageContainer from '@/components/PageContainer';
import { customRequest, type BaseApiResponse } from '@/utils/fetch';

import { convertGraphData, type ForceGraphData, type GraphApiData } from './components/utils';

// TODO: 接全局项目选择后替换为当前项目 id。
const PROJECT_ID = 'codev-platform';
const EMPTY_GRAPH: ForceGraphData = { nodes: [], links: [] };

export default function GraphPage() {
  const [graph, setGraph] = useState<ForceGraphData>(EMPTY_GRAPH);

  const handleLoad = useCallback(async (): Promise<void> => {
    try {
      const res = await customRequest<BaseApiResponse<GraphApiData>>(
        '/api/v1/graph/cross-link/graph',
        {
          method: 'POST',
          data: { mode: 'overview' },
          headers: { 'X-Project-Id': PROJECT_ID },
        },
      );
      setGraph(convertGraphData(res.data));
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
