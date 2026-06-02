// 图谱可视化 —— 接管 codegraph-api 的 force-graph 面 (/api/v1/graph/cross-link/graph)。
// 用 react-force-graph-3d (与原 codegraph 业务页同款依赖) 渲染 cross_layer 全图。
import { post } from '@/utils/fetch';
import { PageContainer, ProCard } from '@ant-design/pro-components';
import { Button, message } from 'antd';
import { useState } from 'react';
import ForceGraph3D from 'react-force-graph-3d';

export default function GraphPage() {
  const [graph, setGraph] = useState<{ nodes: any[]; links: any[] }>({ nodes: [], links: [] });

  const load = async () => {
    try {
      const res: any = await post({
        url: '/api/v1/graph/cross-link/graph',
        data: { mode: 'overview' },
      });
      const nodes = (res.data?.nodes ?? []).map((n: any) => ({ id: n.id, name: n.name, kind: n.kind }));
      const links = (res.data?.edges ?? []).map((e: any) => ({ source: e.source, target: e.target }));
      setGraph({ nodes, links });
    } catch {
      message.error('图谱加载失败 (确认后端 :18088 + 项目已建 cross_link 索引)');
    }
  };

  return (
    <PageContainer>
      <ProCard
        title="跨层链路图 (cross-link)"
        bordered
        extra={
          <Button type="primary" onClick={load}>
            加载全图
          </Button>
        }
      >
        <div style={{ height: 600 }}>
          <ForceGraph3D graphData={graph} nodeAutoColorBy="kind" nodeLabel="name" />
        </div>
      </ProCard>
    </PageContainer>
  );
}
