// 任务中心 —— 提交索引重建 + 按 jobId 查状态 (消费 /api/v1/indexes/rebuild + /api/v1/jobs/detail)。
// 第一版极简 (后端暂无 job list 接口, 只有 detail/cancel/rebuild)。
import { PageContainer, ProCard } from '@ant-design/pro-components';
import { Button, Descriptions, Input, Select, Space, message } from 'antd';
import { useState } from 'react';
import { post, get } from '@/utils/fetch';
import { useModel } from '@umijs/max';

const PROJECT_HEADER = { 'X-Project-Id': 'codev-platform' };

export default function JobsPage() {
  const { getEnumOptions } = useModel('enum');
  const statusOptions = getEnumOptions('JobStatusEnum'); // 仅展示用, 证明枚举打通
  const [kind, setKind] = useState('chroma');
  const [jobId, setJobId] = useState('');
  const [detail, setDetail] = useState<any>(null);

  const submit = async () => {
    const res: any = await post({ url: '/api/v1/indexes/rebuild', data: { indexKind: kind } });
    setJobId(res.data?.jobId);
    message.success(`已提交 jobId=${res.data?.jobId}`);
  };
  const query = async () => {
    const res: any = await get({ url: '/api/v1/jobs/detail', data: { jobId } });
    setDetail(res.data);
  };

  return (
    <PageContainer>
      <ProCard title="提交索引重建" bordered style={{ marginBottom: 16 }}>
        <Space>
          <Select
            value={kind}
            style={{ width: 160 }}
            onChange={setKind}
            options={['chroma', 'codegraph', 'cross_link', 'all'].map((v) => ({ label: v, value: v }))}
          />
          <Button type="primary" onClick={submit}>
            提交 rebuild
          </Button>
        </Space>
      </ProCard>
      <ProCard title="查询任务状态" bordered>
        <Space style={{ marginBottom: 12 }}>
          <Input value={jobId} onChange={(e) => setJobId(e.target.value)} placeholder="jobId" style={{ width: 320 }} />
          <Button onClick={query}>查询</Button>
        </Space>
        {detail && (
          <Descriptions column={2} bordered size="small">
            <Descriptions.Item label="jobId">{detail.jobId}</Descriptions.Item>
            <Descriptions.Item label="状态">{detail.status}</Descriptions.Item>
            <Descriptions.Item label="类型">{detail.jobType}</Descriptions.Item>
            <Descriptions.Item label="项目">{detail.projectId}</Descriptions.Item>
          </Descriptions>
        )}
      </ProCard>
    </PageContainer>
  );
}
