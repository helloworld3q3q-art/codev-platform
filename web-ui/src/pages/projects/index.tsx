// 项目管理 (ProTable) —— 消费 POST /api/v1/projects/list (PageResult)。
import { post } from '@/utils/fetch';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import { Tag } from 'antd';

interface ProjectItem {
  code: string;
  name: string;
  status: string;
  loaded: boolean;
  repoPath?: string;
}

export default function ProjectsPage() {
  // 状态枚举走后端真值源 (useModel('enum')), 不前端硬编码。
  const { getFormattedEnums } = useModel('enum');
  const statusMap = getFormattedEnums('ProjectStatusEnum');

  const columns: ProColumns<ProjectItem>[] = [
    { title: '项目编码', dataIndex: 'code', copyable: true },
    { title: '名称', dataIndex: 'name' },
    {
      title: '状态',
      dataIndex: 'status',
      render: (_, r) => <Tag>{statusMap[r.status] ?? r.status}</Tag>,
    },
    {
      title: '运行态',
      dataIndex: 'loaded',
      render: (_, r) => (r.loaded ? <Tag color="green">已加载</Tag> : <Tag>未加载</Tag>),
    },
    { title: '仓路径', dataIndex: 'repoPath', ellipsis: true },
  ];

  return (
    <PageContainer>
      <ProTable<ProjectItem>
        rowKey="code"
        columns={columns}
        search={false}
        options={{ reload: true, density: false, setting: false }}
        request={async (params) => {
          const res: any = await post({
            url: '/api/v1/projects/list',
            data: { pageNumber: params.current, pageSize: params.pageSize },
          });
          return { data: res.data ?? [], total: res.total ?? 0, success: true };
        }}
        pagination={{ pageSize: 20 }}
      />
    </PageContainer>
  );
}
