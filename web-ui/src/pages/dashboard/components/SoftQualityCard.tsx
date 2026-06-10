// 软标签健康度卡 —— A1 业务域 / A2 架构层软标签的分布 / 覆盖 / 巨型 cluster 退化诊断(soft-quality)。
// 数据来自后端 /api/v1/graph/soft-quality(assess_soft_labels), 纯展示。空软层(未跑 analyzer)= healthy。
// 与「图谱结构健康」(audit, 结构 error)正交: 这里看软标签标得健不健康(覆盖/退化), 非结构断链。
import { ProCard } from '@ant-design/pro-components';
import { Descriptions, Tag } from 'antd';

interface SoftQualityCardProps {
  data?: API.GraphSoftQualityResponse;
}

// 覆盖率后端是 0~1 小数 (labeled/eligible), 缺数据为 null → 展示 '-'。
const formatCoverage = (v?: number): string => {
  if (v === undefined || v === null) {
    return '-';
  }
  return `${Math.round(v * 100)}%`;
};

const SoftQualityCard = ({ data }: SoftQualityCardProps) => {
  const healthy = data?.healthy ?? true;
  const flagCount = data?.flagCount ?? 0;
  return (
    <ProCard
      title="软标签健康度"
      variant="outlined"
      classNames={{ root: 'i:mb-16' }}
      extra={
        healthy ? (
          <Tag color="green">软标签健康</Tag>
        ) : (
          <Tag color="orange">{flagCount} 个退化信号</Tag>
        )
      }
    >
      <Descriptions column={3} size="small">
        <Descriptions.Item label="业务域(A1)节点">{data?.domainCount ?? 0}</Descriptions.Item>
        <Descriptions.Item label="业务域覆盖率">{formatCoverage(data?.domainCoverage)}</Descriptions.Item>
        <Descriptions.Item label="业务域巨型 cluster">{data?.domainGiant ?? 0}</Descriptions.Item>
        <Descriptions.Item label="架构层(A2)节点">{data?.layerCount ?? 0}</Descriptions.Item>
        <Descriptions.Item label="架构层覆盖率">{formatCoverage(data?.layerCoverage)}</Descriptions.Item>
        <Descriptions.Item label="架构层巨型 cluster">{data?.layerGiant ?? 0}</Descriptions.Item>
      </Descriptions>
    </ProCard>
  );
};

export default SoftQualityCard;
