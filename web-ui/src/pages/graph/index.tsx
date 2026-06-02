// 图谱可视化 —— 接管 codegraph-api 的 force-graph 面 (消费 /api/v1/graph/cross-link/graph)。
// 用 react-force-graph-2d 渲染 cross_layer 全图; 多租户带 X-Project-Id。
import { PageContainer, ProCard } from '@ant-design/pro-components';
import { Button, Space, message } from 'antd';
import { useRef, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { post } from '@/utils/fetch';

const PROJECT_HEADER = { 'X-Project-Id': 'codev-platform' };

export default function GraphPage() {
  const [graph, setGraph] = useState<{ nodes: any[]; links: any[] }>({ nodes: [], links: [] });
  const loadedRef = useRef(false);

  const load = async () => {
    try {
      // 注意: post 默认不带自定义 header, 真用时给 fetch 封装加 X-Project-Id 透传 (见 README 待办)。
      const res: any = await post({ url: '/api/v1/graph/cross-link/graph', data: { mode: 'overview' } });
      const nodes = (res.data?.nodes ?? []).map((n: any) => ({ id: n.id, name: n.name, kind: n.kind }));
      const links = (res.data?.edges ?? []).map((e: any) => ({ source: e.source, target: e.target }));
      setGraph({ nodes, links });
      loadedRef.current = true;
    } catch (e) {
      message.error('图谱加载失败 (确认后端 :18088 + 项目已建 cross_link 索引)');
    }
  };

  return (
    <PageContainer>
      <ProCard
        title="跨层链路图 (cross-link)"
        bordered
        extra={
          <Space>
            <Button type="primary" onClick={load}>
              加载全图
            </Button>
          </Space>
        }
      >
        <div style={{ height: 600 }}>
          <ForceGraph2D graphData={graph} nodeAutoColorBy="kind" nodeLabel="name" />
        </div>
      </ProCard>
    </PageContainer>
  );
}
