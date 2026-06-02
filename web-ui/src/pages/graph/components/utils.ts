// 图谱数据转换：后端 cross-link {nodes,edges} → react-force-graph-3d {nodes,links}。
// 架构规则：数据转换下沉 utils，index.tsx 只负责编排。

// 后端 cross-link 图响应节点形状（POST /api/v1/graph/cross-link/graph mode=overview）。
export interface GraphApiNode {
  id: string;
  name: string;
  kind: string;
}

// 后端边形状。
export interface GraphApiEdge {
  source: string;
  target: string;
}

export interface GraphApiData {
  nodes?: GraphApiNode[];
  edges?: GraphApiEdge[];
}

// force-graph 节点（保留 kind 供 nodeAutoColorBy 上色 / nodeLabel 展示）。
export interface ForceGraphNode {
  id: string;
  name: string;
  kind: string;
}

// force-graph 连线（source/target 引用节点 id）。
export interface ForceGraphLink {
  source: string;
  target: string;
}

export interface ForceGraphData {
  nodes: ForceGraphNode[];
  links: ForceGraphLink[];
}

// 把后端 nodes/edges 转成 react-force-graph-3d 需要的 {nodes,links}。
// 显式函数体（禁箭头隐式返回），空值兜底为空图，避免 force-graph 拿到 undefined 报错。
export function convertGraphData(resData: GraphApiData | undefined): ForceGraphData {
  const rawNodes = resData?.nodes ?? [];
  const rawEdges = resData?.edges ?? [];

  const nodes: ForceGraphNode[] = rawNodes.map((node): ForceGraphNode => {
    return { id: node.id, name: node.name, kind: node.kind };
  });

  const links: ForceGraphLink[] = rawEdges.map((edge): ForceGraphLink => {
    return { source: edge.source, target: edge.target };
  });

  return { nodes, links };
}
