// 索引新鲜度卡 —— 各类索引(chroma/codegraph/graph/docs)相对当前 HEAD 的新鲜度。
// 数据来自统一 IndexManifest(后端 /api/v1/indexes/status, Phase 1), 纯展示不触发构建。
import { ProCard } from '@ant-design/pro-components';
import { Empty, Tag, Typography } from 'antd';
import dayjs from 'dayjs';

interface IndexFreshnessCardProps {
  data?: API.IndexStatusResponse;
}

// 新鲜度: 对齐 HEAD=绿 / 落后=橙 / 未知(无 repo 或拿不到 HEAD)=灰。
function renderFreshTag(fresh?: boolean) {
  if (fresh === true) {
    return <Tag color="green">对齐 HEAD</Tag>;
  }
  if (fresh === false) {
    return <Tag color="orange">落后</Tag>;
  }
  return <Tag>未知</Tag>;
}

const IndexFreshnessCard = ({ data }: IndexFreshnessCardProps) => {
  const items = data?.items ?? [];
  const head = data?.headCommit;
  return (
    <ProCard
      title="索引新鲜度"
      variant="outlined"
      classNames={{ root: 'i:mb-16' }}
      extra={
        head ? (
          <Typography.Text className="text-12 text-#8c8c8c">HEAD {head.slice(0, 8)}</Typography.Text>
        ) : null
      }
    >
      {items.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无索引构建记录(reindex 跑过后即有)"
        />
      ) : (
        <div className="flex flex-col gap-8">
          {items.map((it, idx) => (
            <div key={it.kind ?? idx} className="flex items-center gap-12">
              <span className="w-80 font-600 text-13">{it.kind ?? '-'}</span>
              <Tag color={it.status === 'ok' ? 'green' : 'red'}>{it.status ?? '-'}</Tag>
              {renderFreshTag(it.fresh)}
              <span className="text-12 text-#8c8c8c">
                commit {it.gitCommit ? it.gitCommit.slice(0, 8) : '—'}
              </span>
              <span className="text-12 text-#8c8c8c">
                {it.finishedAt ? dayjs(it.finishedAt * 1000).format('YYYY/MM/DD HH:mm') : '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </ProCard>
  );
};

export default IndexFreshnessCard;
