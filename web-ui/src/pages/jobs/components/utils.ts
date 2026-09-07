// 任务中心常量 + 请求参数转换。
// indexKind 选项是触发 rebuild 的有限离散参数 (chroma/codegraph/all),
// 非跨层业务枚举 (不进 DTO 投影 / 无 Java 真值源), 按"页面触发参数"本地常量处理,
// 符合 cross-layer-enum-consistency §8 例外 (当前页面专用配置)。
export const INDEX_KIND_OPTIONS: { label: string; value: string }[] = [
  { label: 'all', value: 'all' },
  { label: 'chroma', value: 'chroma' },
  { label: 'codegraph', value: 'codegraph' },
];

// 提交 rebuild 请求体 (POST /api/v1/indexes/rebuild)。
export const convertRebuildParams = (indexKind: string): { indexKind: string } => {
  const params = { indexKind };
  return params;
};

// 查询任务详情请求参数 (GET /api/v1/jobs/detail)。
export const convertDetailParams = (jobId: string): { jobId: string } => {
  const params = { jobId: jobId.trim() };
  return params;
};
