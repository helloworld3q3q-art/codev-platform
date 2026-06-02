// 枚举元数据演示 —— useModel('enum') (来自 POST /api/v1/enums/list)。
import { PageContainer, ProCard } from '@ant-design/pro-components';
import { useModel } from '@umijs/max';
import { Table, Tag } from 'antd';

export default function EnumsPage() {
  const { enumsGroup } = useModel('enum');
  const types = Object.keys(enumsGroup || {});

  return (
    <PageContainer>
      {types.map((t) => (
        <ProCard key={t} title={t} bordered collapsible style={{ marginBottom: 16 }}>
          <Table
            rowKey="enumValue"
            size="small"
            pagination={false}
            dataSource={enumsGroup[t]}
            columns={[
              { title: 'value', dataIndex: 'enumValue', render: (v) => <Tag>{v}</Tag> },
              { title: '中文', dataIndex: 'localLanguage' },
              { title: '排序', dataIndex: 'enumOrder', width: 80 },
              { title: '说明', dataIndex: 'description' },
            ]}
          />
        </ProCard>
      ))}
    </PageContainer>
  );
}
