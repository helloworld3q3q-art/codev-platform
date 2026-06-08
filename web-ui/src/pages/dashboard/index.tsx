// 仪表盘首页 —— 平台健康 + 当前项目图谱统计 + 资源计数概览。
import { useCallback, useEffect, useState } from 'react';
import { useModel } from '@umijs/max';

import { ProCard, StatisticCard } from '@ant-design/pro-components';
import { Descriptions, Spin, Tag } from 'antd';

import PageContainer from '@/components/PageContainer';
import { isAdminRole } from '@/utils/role';

import GraphHealthCard from './components/GraphHealthCard';
import IndexFreshnessCard from './components/IndexFreshnessCard';
import McpUsageCard from './components/McpUsageCard';
import { DASHBOARD_DEFAULT, loadDashboard, type DashboardData } from './components/utils';

export default function DashboardPage() {
  // 订阅当前项目, 切项目后图谱统计原地重拉(KeepAlive 缓存页 mount 仍按旧 X-Project-Id)。
  const { currentProjectId } = useModel('project');
  // MCP 调用分析仅管理员可见。roles 取 initialState (app.tsx getSession 下发的权威源,
  // 与菜单/access 同口径);不读 useModel('user'), 因登录只往 localStorage 存 username 无 roles。
  const { initialState } = useModel('@@initialState');
  const isAdmin = isAdminRole(initialState?.userInfo?.roles);
  const [data, setData] = useState<DashboardData>(DASHBOARD_DEFAULT);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const next = await loadDashboard(isAdmin);
      setData(next);
    } finally {
      setLoading(false);
    }
    // currentProjectId / 角色变化触发重拉
  }, [currentProjectId, isAdmin]);

  useEffect(() => {
    load();
  }, [load]);

  const { health, codegraph, unified, mcpUsage, indexStatus, graphAudit, projectCount, orgCount } =
    data;
  const healthOk = health?.status === 'ok';
  const depItems = Object.entries(health?.dependencies ?? {});

  return (
    <PageContainer>
      <Spin spinning={loading}>
        <StatisticCard.Group direction="row" className="i:mb-16">
          <StatisticCard statistic={{ title: '项目数', value: projectCount }} />
          <StatisticCard statistic={{ title: '组织数', value: orgCount }} />
          <StatisticCard statistic={{ title: 'CodeGraph 节点', value: codegraph?.totalNodes ?? 0 }} />
          <StatisticCard statistic={{ title: '统一图谱 节点', value: unified?.totalNodes ?? 0 }} />
          <StatisticCard statistic={{ title: '统一图谱 边', value: unified?.totalEdges ?? 0 }} />
        </StatisticCard.Group>

        <ProCard title="平台健康" variant="outlined" classNames={{ root: 'i:mb-16' }}>
          <Descriptions column={2} size="small">
            <Descriptions.Item label="状态">
              <Tag color={healthOk ? 'green' : 'red'}>{health?.status ?? '未知'}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="服务">{health?.service ?? '-'}</Descriptions.Item>
            {depItems.map(([name, state]) => (
              <Descriptions.Item key={name} label={name}>
                {state}
              </Descriptions.Item>
            ))}
          </Descriptions>
        </ProCard>

        <ProCard title="当前项目图谱" variant="outlined" classNames={{ root: 'i:mb-16' }}>
          <Descriptions column={3} size="small">
            <Descriptions.Item label="CodeGraph 文件">{codegraph?.totalFiles ?? 0}</Descriptions.Item>
            <Descriptions.Item label="CodeGraph 节点">{codegraph?.totalNodes ?? 0}</Descriptions.Item>
            <Descriptions.Item label="CodeGraph 边">{codegraph?.totalEdges ?? 0}</Descriptions.Item>
            <Descriptions.Item label="统一图谱 节点">{unified?.totalNodes ?? 0}</Descriptions.Item>
            <Descriptions.Item label="统一图谱 边">{unified?.totalEdges ?? 0}</Descriptions.Item>
            <Descriptions.Item label="统一图谱 节点类">
              {Object.keys(unified?.nodesByKind ?? {}).length}
            </Descriptions.Item>
            <Descriptions.Item label="统一图谱 边类">
              {Object.keys(unified?.edgesByKind ?? {}).length}
            </Descriptions.Item>
          </Descriptions>
        </ProCard>

        <IndexFreshnessCard data={indexStatus} />

        <GraphHealthCard data={graphAudit} />

        {isAdmin && <McpUsageCard data={mcpUsage} />}
      </Spin>
    </PageContainer>
  );
}
