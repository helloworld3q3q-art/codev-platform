// 图谱可视化 —— 接管 cross-link 的 force-graph 面 (POST /api/v1/graph/cross-link/graph)。
// 用 react-force-graph-3d 渲染 cross_layer overview 全图。数据转换下沉 components/utils.ts。
// X-Project-Id 由 fetch 拦截器全局注入，本页只调生成 service。
// 无索引项目: 后端返回空(200), 本页显友好空状态而非报错。
import { useCallback, useState } from 'react';
import { ProCard } from '@ant-design/pro-components';
import { Button, Empty } from 'antd';
import ForceGraph3D from 'react-force-graph-3d';

import PageContainer from '@/components/PageContainer';
import { postGraph2 } from '@/services/apis/graphapi';

import { convertGraphData, type ForceGraphData, type GraphApiData } from './components/utils';

const EMPTY_GRAPH: ForceGraphData = { nodes: [], links: [] };

export default function GraphPage() {
  const [graph, setGraph] = useState<ForceGraphData>(EMPTY_GRAPH);
  const [loaded, setLoaded] = useState(false);

  const handleLoad = useCallback(async (): Promise<void> => {
    try {
      const res = await postGraph2({ mode: 'overview' });
      setGraph(convertGraphData(res.data as GraphApiData | undefined));
    } catch {
      // 无索引现在后端返回空(200)不进此分支; 真错误(后端不可用等)已由 fetch 统一弹错。
      setGraph(EMPTY_GRAPH);
    } finally {
      setLoaded(true);
    }
  }, []);

  const isEmpty = loaded && graph.nodes.length === 0;

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
          {isEmpty ? (
            <Empty
              className="pt-120"
              description="该项目暂无跨层链路索引或无数据；请先建索引，或在顶栏切换到已建索引的项目。"
            />
          ) : (
            <ForceGraph3D graphData={graph} nodeAutoColorBy="kind" nodeLabel="name" />
          )}
        </div>
      </ProCard>
    </PageContainer>
  );
}
