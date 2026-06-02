// 任务中心 —— 提交索引重建 (POST /api/v1/indexes/rebuild) + 按 jobId 查状态 (GET /api/v1/jobs/detail)。
// 操作页 (非列表 CRUD): 提交区 + 查询区。状态中文走后端真值源 JobStatusEnum (useModel('enum'))。
import { useCallback, useState } from 'react';
import { useModel } from '@umijs/max';

import { Button, Descriptions, Input, Select, Space, Tag, message } from 'antd';
import { ProCard } from '@ant-design/pro-components';

import PageContainer from '@/components/PageContainer';
import { postRebuild } from '@/services/apis/indexapi';
import { getDetail } from '@/services/apis/jobapi';

import {
  INDEX_KIND_OPTIONS,
  convertDetailParams,
  convertRebuildParams,
} from './components/utils';

// 查询区关联 state (jobId 输入 + 拉回的 detail 总是一起重置) 合并为一个对象。
interface QueryState {
  jobId: string;
  detail?: API.JobDTO;
}

const QUERY_DEFAULT: QueryState = { jobId: '', detail: undefined };

export default function JobsPage() {
  // 状态枚举走后端真值源 (useModel('enum')), 不前端硬编码中文。
  const { getFormattedEnums } = useModel('enum');
  const statusMap = getFormattedEnums('JobStatusEnum');

  const [indexKind, setIndexKind] = useState<string>('all');
  const [query, setQuery] = useState<QueryState>(QUERY_DEFAULT);

  const handleKindChange = useCallback((value: string): void => {
    setIndexKind(value);
  }, []);

  const handleJobIdChange = useCallback((e: React.ChangeEvent<HTMLInputElement>): void => {
    const value = e.target.value;
    setQuery((prev) => ({ ...prev, jobId: value }));
  }, []);

  const handleSubmit = useCallback(async (): Promise<void> => {
    try {
      const res = await postRebuild(convertRebuildParams(indexKind));
      const newJobId = res.data?.jobId ?? '';
      setQuery({ jobId: newJobId, detail: undefined });
      message.success(`已提交 jobId=${newJobId}`);
    } catch {
      // 错误已由 fetch 统一处理
    }
  }, [indexKind]);

  const handleQuery = useCallback(async (): Promise<void> => {
    if (!query.jobId.trim()) return;
    try {
      const res = await getDetail(convertDetailParams(query.jobId));
      setQuery((prev) => ({ ...prev, detail: res.data }));
    } catch {
      // 错误已由 fetch 统一处理
    }
  }, [query.jobId]);

  const { detail } = query;

  return (
    <PageContainer>
      <ProCard title="提交索引重建" bordered classNames={{ root: 'i:mb-16' }}>
        <Space>
          <Select
            className="w-160"
            value={indexKind}
            onChange={handleKindChange}
            options={INDEX_KIND_OPTIONS}
          />
          <Button type="primary" onClick={handleSubmit}>
            提交 rebuild
          </Button>
        </Space>
      </ProCard>

      <ProCard title="查询任务状态" bordered>
        <Space className="mb-12">
          <Input
            className="w-320"
            value={query.jobId}
            onChange={handleJobIdChange}
            placeholder="jobId"
          />
          <Button onClick={handleQuery}>查询</Button>
        </Space>
        {detail && (
          <Descriptions column={2} bordered size="small">
            <Descriptions.Item label="jobId">{detail.jobId}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag>{statusMap[detail.status] ?? detail.status}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="类型">{detail.jobType}</Descriptions.Item>
            <Descriptions.Item label="项目">{detail.projectId}</Descriptions.Item>
          </Descriptions>
        )}
      </ProCard>
    </PageContainer>
  );
}
