// 图谱结构健康卡 —— 当前项目统一图谱的结构审计摘要(Phase 3)。
// 数据来自后端 /api/v1/graph/audit(复用 audit_graph), 纯展示。
// errors(断链/串台/孤儿 plugin)阻断, warnings(重复/低置信)待 review。
import { ProCard } from '@ant-design/pro-components';
import { Descriptions, Tag } from 'antd';

interface GraphHealthCardProps {
  data?: API.GraphAuditResponse;
}

const GraphHealthCard = ({ data }: GraphHealthCardProps) => {
  const clean = data?.clean ?? true;
  const errorCount = data?.errorCount ?? 0;
  return (
    <ProCard
      title="图谱结构健康"
      variant="outlined"
      classNames={{ root: 'i:mb-16' }}
      extra={
        clean ? <Tag color="green">结构健康</Tag> : <Tag color="red">{errorCount} 个结构 error</Tag>
      }
    >
      <Descriptions column={4} size="small">
        <Descriptions.Item label="断链边">{data?.danglingEdges ?? 0}</Descriptions.Item>
        <Descriptions.Item label="跨租户串台">{data?.crossProjectNodes ?? 0}</Descriptions.Item>
        <Descriptions.Item label="孤儿 plugin">{data?.orphanSoftPlugins ?? 0}</Descriptions.Item>
        <Descriptions.Item label="重复节点">{data?.duplicateNodes ?? 0}</Descriptions.Item>
        <Descriptions.Item label="低置信硬边">{data?.lowConfidenceEdges ?? 0}</Descriptions.Item>
        <Descriptions.Item label="节点">{data?.nodes ?? 0}</Descriptions.Item>
        <Descriptions.Item label="边">{data?.edges ?? 0}</Descriptions.Item>
      </Descriptions>
    </ProCard>
  );
};

export default GraphHealthCard;
