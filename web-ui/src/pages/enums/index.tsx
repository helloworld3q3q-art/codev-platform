// 枚举元数据演示 —— useModel('enum') 复用 stock-admin-web 模式, 数据来自 POST /api/v1/enums/list。
// 证明前端 enum model 零改对接 codev-platform 后端。
import { PageContainer, ProCard } from '@ant-design/pro-components';
import { Table, Tag } from 'antd';
import { useModel } from '@umijs/max';

export default function EnumsPage() {
  const { enumsGroup } = useModel('enum');
  const types = Object.keys(enumsGroup || {});

  return (
    <PageContainer>
      {types.map((t) => (
        <ProCard key={t} title={t} bordered style={{ marginBottom: 16 }} collapsible>
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
