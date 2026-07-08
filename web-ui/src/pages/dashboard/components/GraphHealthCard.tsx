// 图谱结构健康卡 —— 当前项目统一图谱的结构审计摘要(Phase 3)。
// 数据来自后端 /api/v1/graph/audit(复用 audit_graph), 纯展示。
// errors(断链/串台/孤儿 plugin)阻断, warnings(重复/低置信/API 链路覆盖)待 review。
import { ProCard } from '@ant-design/pro-components';
import { Descriptions, Tag } from 'antd';

interface GraphHealthCardProps {
  data?: API.GraphAuditResponse;
}

const apiCoverageColor = (data?: API.GraphAuditResponse): string => {
  if ((data?.invalidCallsApiEdges ?? 0) > 0) {
    return 'red';
  }
  if ((data?.unlinkedFrontendApiCalls ?? 0) > 0) {
    return 'orange';
  }
  if ((data?.frontendApiCalls ?? 0) > 0) {
    return 'green';
  }
  return 'default';
};

const apiCoverageText = (data?: API.GraphAuditResponse): string => {
  const frontend = data?.frontendApiCalls ?? 0;
  if (!frontend) {
    return '无前端 API';
  }
  return `${data?.linkedFrontendApiCalls ?? 0}/${frontend} 已链接`;
};

const shouldShowApiCoverage = (data?: API.GraphAuditResponse): boolean => {
  return Boolean(
    (data?.frontendApiCalls ?? 0) > 0 ||
      (data?.backendEndpoints ?? 0) > 0 ||
      (data?.invalidCallsApiEdges ?? 0) > 0,
  );
};

const GraphHealthCard = ({ data }: GraphHealthCardProps) => {
  const clean = data?.clean ?? true;
  const errorCount = data?.errorCount ?? 0;
  const showApiCoverage = shouldShowApiCoverage(data);
  const apiWarning = (data?.unlinkedFrontendApiCalls ?? 0) > 0 || (data?.invalidCallsApiEdges ?? 0) > 0;
  return (
    <ProCard
      title="图谱结构健康"
      variant="outlined"
      classNames={{ root: 'i:mb-16' }}
      extra={
        <>
          {clean ? <Tag color="green">结构健康</Tag> : <Tag color="red">{errorCount} 个结构 error</Tag>}
          {showApiCoverage && <Tag color={apiCoverageColor(data)}>API {apiCoverageText(data)}</Tag>}
        </>
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
        {showApiCoverage && (
          <>
            <Descriptions.Item label="API 链路">{apiCoverageText(data)}</Descriptions.Item>
            <Descriptions.Item label="后端 endpoint">{data?.backendEndpoints ?? 0}</Descriptions.Item>
            <Descriptions.Item label="有效 calls_api">{data?.callsApiEdges ?? 0}</Descriptions.Item>
            <Descriptions.Item label="畸形 calls_api">{data?.invalidCallsApiEdges ?? 0}</Descriptions.Item>
            {apiWarning && (
              <Descriptions.Item label="API 诊断" span={4}>
                {data?.apiLinkDiagnosis || data?.apiLinkBrief || '-'}
              </Descriptions.Item>
            )}
          </>
        )}
      </Descriptions>
    </ProCard>
  );
};

export default GraphHealthCard;
