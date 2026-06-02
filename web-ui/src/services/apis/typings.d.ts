declare namespace API {
      
  // any 类型定义
type any = any;

// 信号回验汇总分页查询请求
interface ValidationSummaryRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  signal?: string; // 信号筛选，传 ALL 查合并统计行，传 BUY/SELL 查分信号统计
  forwardDays?: number; // 验证周期筛选（交易日数），如 5 / 10 / 20
  positionSizingMode?: string; // 按 mode 过滤：formula / fixed，null 表示全部
}

// ApiError 接口
interface ApiError {
  errorCode?: string;
  errorMessage?: string;
  referenceData?: string;
}

// 按 mode 分桶的命中率/平均收益
interface ModeBreakdown {
  formulaHitRate?: number; // formula mode 命中率
  formulaAvgReturn?: number; // formula mode 平均收益率
  formulaSampleCount?: number; // formula mode 样本数
  fixedHitRate?: number; // fixed mode 命中率
  fixedAvgReturn?: number; // fixed mode 平均收益率
  fixedSampleCount?: number; // fixed mode 样本数
}

// PageResultValidationSummaryResponse 响应数据
interface PageResultValidationSummaryResponse {
  result?: number;
  message?: string;
  data?: ValidationSummaryResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 信号回验汇总响应
interface ValidationSummaryResponse {
  validationDate?: string; // 验证执行日期
  forwardDays?: number; // 验证周期（交易日数）
  signal?: string; // 信号类型，null 表示所有信号合并统计
  hitRate?: number; // 命中率 = n_correct / n_total
  avgReturn?: number; // 所有信号的平均实际收益率
  avgCorrectReturn?: number; // 命中信号的平均收益率
  avgWrongReturn?: number; // 失误信号的平均收益率
  suggestions?: string; // 调整建议列表 JSON 字符串
  positionSizingMode?: string; // 当时生效的仓位算法 mode: formula / fixed（V202605240200 起入库）
  modeBreakdown?: ModeBreakdown;
  ntotal?: number;
  ncorrect?: number;
}

// CommonResultListValidationSummaryResponse 响应数据
interface CommonResultListValidationSummaryResponse {
  result?: number;
  message?: string;
  data?: ValidationSummaryResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 信号回验明细分页查询请求
interface ValidationPageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  stockCode?: string; // 股票代码筛选
  signal?: string; // 信号筛选：BUY / STRONG_BUY / SELL
  forwardDays?: number; // 验证周期筛选（交易日数），如 5 / 10 / 20
}

// PageResultValidationDetailResponse 响应数据
interface PageResultValidationDetailResponse {
  result?: number;
  message?: string;
  data?: ValidationDetailResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 信号回验明细响应
interface ValidationDetailResponse {
  id?: number; // 明细 ID
  validationDate?: string; // 验证执行日期
  analysisDate?: string; // 原始分析日期
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  signal?: string; // 原始信号（BUY / STRONG_BUY / SELL）
  score?: number; // 原始评分
  forwardDays?: number; // 验证周期（交易日数）
  entryPrice?: number; // 分析日收盘价（建仓参考价）
  exitPrice?: number; // 前向 N 日收盘价（退出参考价）
  actualReturn?: number; // 实际收益率（exit/entry - 1）
  isCorrect?: boolean; // 信号方向是否正确
}

// 更新用户基本信息请求
interface UserUpdateRequest {
  id: number; // 用户 ID
  displayName?: string; // 显示昵称
  email?: string; // 邮箱
  avatar?: string; // 头像 URL
}

// CommonResultVoid 接口
interface CommonResultVoid {
  result?: number;
  message?: string;
  data?: Record<string, any>;
  errors?: ApiError[];
  ok?: boolean;
}

// 重置用户密码请求
interface ResetPasswordRequest {
  userId: number; // 目标用户 ID
  newPassword: string; // 新密码（明文）
}

// 用户分页查询请求
interface UserPageRequest {
  keyword?: string; // 关键词（匹配用户名或昵称）
  status?: string; // 状态过滤：ACTIVE / DISABLED / LOCKED
  page?: number; // 页码（从 1 开始）
  pageSize?: number; // 每页条数
}

// PageResultUserPageResponse 响应数据
interface PageResultUserPageResponse {
  result?: number;
  message?: string;
  data?: UserPageResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 用户列表单条数据
interface UserPageResponse {
  id?: number; // 用户 ID
  username?: string; // 登录用户名
  displayName?: string; // 显示昵称
  email?: string; // 邮箱
  avatar?: string; // 头像 URL
  status?: string; // 状态：ACTIVE / DISABLED / LOCKED
  roles?: string[]; // 已绑定角色 code 列表
  lastLoginAt?: string; // 最近一次登录时间
  createdAt?: string; // 创建时间
}

// 用户启用/禁用请求
interface UserToggleRequest {
  userId: number; // 用户主键 ID
}

// 创建用户请求
interface UserCreateRequest {
  username: string; // 登录用户名（全局唯一）
  password: string; // 初始密码（明文，服务端 BCrypt 加密）
  displayName?: string; // 显示昵称
  email?: string; // 邮箱
  roleIds?: number[]; // 初始分配的角色 ID 列表
}

// CommonResultLong 接口
interface CommonResultLong {
  result?: number;
  message?: string;
  data?: number;
  errors?: ApiError[];
  ok?: boolean;
}

// 为用户分配角色请求
interface AssignRolesRequest {
  userId: number; // 目标用户 ID
  roleIds?: number[]; // 角色 ID 列表（空列表表示清空所有角色）
}

// CommonResultMarketRegimeDailyResponse 响应数据
interface CommonResultMarketRegimeDailyResponse {
  result?: number;
  message?: string;
  data?: MarketRegimeDailyResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 市场环境多因子日快照响应
interface MarketRegimeDailyResponse {
  id?: number; // 主键 ID
  tradeDate?: string; // 交易日期（yyyy-MM-dd）
  regime?: string; // 市场环境枚举值：BULL_MARKET / BEAR_MARKET / DEFAULT
  ma200Vote?: number; // MA200 趋势票：-1（空头）/ 0（中性）/ +1（多头）
  volatilityVote?: number; // 波动率票：-1（高波动熊市信号）/ 0 / +1（低波动牛市信号）
  breadthVote?: number; // 市场宽度票：-1（多数股下跌）/ 0 / +1（多数股上涨）
  finalScore?: number; // 三票之和（范围 -3..+3）；>=2 判牛市，<=-2 判熊市
  hs300Close?: number; // 沪深 300 当日收盘价
  hs300Ma200?: number; // 沪深 300 200 日均线
  volatility60d?: number; // 60 日年化波动率（小数，0.18 = 18%）
  upRatio?: number; // 全市场上涨家数 / 总数（小数，0.55 = 55% 股票上涨）
  createdAt?: string; // 记录创建时间
}

// 市场环境切换请求
interface MarketRegimeUpdateRequest {
  regime: string; // 市场环境枚举值（BULL_MARKET / BEAR_MARKET / DEFAULT）
}

// CommonResultMarketRegimeResponse 响应数据
interface CommonResultMarketRegimeResponse {
  result?: number;
  message?: string;
  data?: MarketRegimeResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 市场环境响应
interface MarketRegimeResponse {
  regime?: string; // 市场环境枚举值（BULL_MARKET / BEAR_MARKET / DEFAULT）
  updatedAt?: string; // 最近一次更新时间（ISO-8601）；从未配置时为 null
}

// 市场环境历史查询请求
interface MarketRegimeHistoryRequest {
  days?: number; // 查询最近 N 天，默认 30；最大 365
}

// CommonResultListMarketRegimeDailyResponse 响应数据
interface CommonResultListMarketRegimeDailyResponse {
  result?: number;
  message?: string;
  data?: MarketRegimeDailyResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// CommonResultListDataSourceQualityResponse 响应数据
interface CommonResultListDataSourceQualityResponse {
  result?: number;
  message?: string;
  data?: DataSourceQualityResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 数据源质量评分响应（单数据源运行指标）
interface DataSourceQualityResponse {
  sourceName?: string; // 数据源名称
  totalAttempts?: number; // 累计请求次数
  totalSuccesses?: number; // 累计成功次数
  qualityScore?: number; // 质量分 [0, 1]，越高越优先
  consecutiveFailures?: number; // 当前连续失败次数（重启时清零）
  lastFailureTime?: string; // 上次失败时间 yyyy-MM-ddTHH:mm:ss
  successRate?: number; // 派生属性：成功率 [0, 1]，由 totalSuccesses / totalAttempts 计算
}

// 股票代码请求
interface StockCodeRequest {
  stockCode: string; // 股票代码
}

// CommonResultListStockValuationResponse 响应数据
interface CommonResultListStockValuationResponse {
  result?: number;
  message?: string;
  data?: StockValuationResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 股票估值日快照响应
interface StockValuationResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  tradeDate?: string; // 交易日期
  peRatio?: number; // 市盈率
  pbRatio?: number; // 市净率
  psRatio?: number; // 市销率
  dividendYield?: number; // 股息率
  totalMarketCap?: number; // 总市值
  circulatingMarketCap?: number; // 流通市值
}

// 日线行情查询请求
interface StockQuoteRequest {
  stockCode: string; // 股票代码
  startDate?: string; // 开始日期，格式 yyyy-MM-dd
  endDate?: string; // 结束日期，格式 yyyy-MM-dd
}

// CommonResultListStockQuoteDailyResponse 响应数据
interface CommonResultListStockQuoteDailyResponse {
  result?: number;
  message?: string;
  data?: StockQuoteDailyResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 日线行情响应
interface StockQuoteDailyResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  tradeDate?: string; // 交易日期
  openPrice?: number; // 开盘价
  highPrice?: number; // 最高价
  lowPrice?: number; // 最低价
  closePrice?: number; // 收盘价
  volume?: number; // 成交量
  amount?: number; // 成交额
  dataSource?: string; // 数据源
}

// 股票列表分页请求
interface StockPageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  stockCode?: string; // 股票代码，支持精确或模糊匹配
  stockName?: string; // 股票名称，支持模糊匹配
  market?: string; // 市场筛选，例如 SH、SZ、BJ
  riskLevel?: string; // 最新分析风险等级筛选，例如 LOW、MEDIUM、HIGH
  industries?: string[]; // 行业多选过滤（按 stock_info.industry 精确匹配）
  minMarketCap?: number; // 总市值下限（亿元，仅占位，后续批次启用）
  maxMarketCap?: number; // 总市值上限（亿元，仅占位，后续批次启用）
  minNorthBoundRatio?: number; // 北向占流通股比下限（%，仅占位，后续批次启用）
  includeNonActive?: boolean; // 是否包含已退市/已排除的股票（默认 false 只查 ACTIVE；仅当未指定 status 时生效）
  status?: string; // 股票状态精确过滤：ACTIVE / DELISTED / EXCLUDED；为空时按 includeNonActive 行为
}

// PageResultStockPageItemResponse 响应数据
interface PageResultStockPageItemResponse {
  result?: number;
  message?: string;
  data?: StockPageItemResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 股票列表行响应
interface StockPageItemResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称
  market?: string; // 所属市场
  industry?: string; // 所属行业
  latestTradeDate?: string; // 最新行情交易日
  latestClose?: number; // 最新收盘价
  marketCap?: number; // 总市值（元），来自 stock_info 流动性快照
  circMarketCap?: number; // 流通市值（元），来自 stock_info 流动性快照
  avgAmount60d?: number; // 60 日均成交额（元），用于流动性判断
  score?: number; // 最新分析评分
  signal?: string; // 最新分析信号
  riskLevel?: string; // 最新风险等级
  status?: string; // 股票状态：ACTIVE/DELISTED/EXCLUDED
}

// 北向资金持股查询请求
interface NorthBoundQueryRequest {
  stockCode: string; // 股票代码
  days?: number; // 向前查询的自然日数，1-365，默认 30
}

// CommonResultListNorthBoundHoldingResponse 响应数据
interface CommonResultListNorthBoundHoldingResponse {
  result?: number;
  message?: string;
  data?: NorthBoundHoldingResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 北向资金持股快照响应（单交易日）
interface NorthBoundHoldingResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  tradeDate?: string; // 交易日期 yyyy-MM-dd
  holdingShares?: number; // 北向持股数（股）
  holdingMarketCap?: number; // 北向持股市值（元）
  holdingRatio?: number; // 占流通股比例（%），> 5% 视为高度关注
  holdingChange?: number; // 较前日持股变动（股），正值表示净流入
  holdingChangeRatio?: number; // 持股变动占流通比（%）
  dataSource?: string; // 数据源
}

// 融资融券历史查询请求
interface MarginTradingHistoryRequest {
  stockCode: string; // 股票代码，如 000001
  days?: number; // 查询最近多少天，默认 60，最大 120
}

// CommonResultMarginTradingHistoryResponse 响应数据
interface CommonResultMarginTradingHistoryResponse {
  result?: number;
  message?: string;
  data?: MarginTradingHistoryResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 单日融资融券数据点
interface MarginPoint {
  tradeDate?: string; // 交易日期 YYYY-MM-DD
  financingBalance?: number; // 融资余额（元）
  financingBuy?: number; // 融资买入额（元）
  financingRepay?: number; // 融资偿还额（元）
  securitiesLendingBalance?: number; // 融券余额（股）
  marginTotalBalance?: number; // 两融余额合计（元）
  dailyNetInflow?: number; // 当日融资净流入（优先融资买入 - 融资偿还；偿还缺失时用融资余额日差推导，元）
}

// 区间汇总
interface MarginSummary {
  totalDays?: number; // 实际返回的交易日总数
  latestFinancingBalance?: number; // 最新融资余额（元）
  periodNetInflow?: number; // 区间累计净流入（累加单日融资净流入，元）
  trend?: string; // 区间融资余额趋势：INCREASING / NEUTRAL / DECREASING
}

// 融资融券历史响应
interface MarginTradingHistoryResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  points?: MarginPoint[]; // 数据点列表（按日期升序）
  summary?: MarginSummary;
}

// CommonResultListStockIntradayQuoteResponse 响应数据
interface CommonResultListStockIntradayQuoteResponse {
  result?: number;
  message?: string;
  data?: StockIntradayQuoteResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 盘中实时报价快照响应
interface StockIntradayQuoteResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  quoteTime?: string; // 报价时间
  currentPrice?: number; // 现价
  changePct?: number; // 涨跌幅（%）
  volume?: number; // 当日成交量（手）
}

// CommonResultListStockIntradaySessionResponse 响应数据
interface CommonResultListStockIntradaySessionResponse {
  result?: number;
  message?: string;
  data?: StockIntradaySessionResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 盘中分段日级聚合响应
interface StockIntradaySessionResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  tradeDate?: string; // 交易日期
  morningOpenPrice?: number; // 早盘开盘价
  morningHighPrice?: number; // 早盘最高价
  morningLowPrice?: number; // 早盘最低价
  morningClosePrice?: number; // 早盘收盘价
  morningVolume?: number; // 早盘成交量，按分钟线累计量差分
  morningAmount?: number; // 早盘成交额，按分钟线累计额差分
  afternoonOpenPrice?: number; // 午盘开盘价
  afternoonHighPrice?: number; // 午盘最高价
  afternoonLowPrice?: number; // 午盘最低价
  afternoonClosePrice?: number; // 午盘收盘价
  afternoonVolume?: number; // 午盘成交量，按分钟线累计量差分
  afternoonAmount?: number; // 午盘成交额，按分钟线累计额差分
  tailOpenPrice30m?: number; // 尾盘 30 分钟起点价
  tailClosePrice30m?: number; // 尾盘 30 分钟收盘价
  openGap?: number; // 早盘开盘相对前收跳空幅度，小数表示
  morningReturn?: number; // 早盘分段收益，小数表示
  afternoonReturn?: number; // 午盘分段收益，小数表示
  intradayReversal?: number; // 午盘收盘相对早盘收盘的反转幅度，小数表示
  tailReturn30m?: number; // 尾盘 30 分钟收益，小数表示
  morningVolumeRatio?: number; // 早盘成交量占全天比例，小数表示
  afternoonVolumeRatio?: number; // 午盘成交量占全天比例，小数表示
  sessionPattern?: string; // 盘中结构标签
  dataSource?: string; // 数据源
}

// CommonResultListStockIndicatorDailyResponse 响应数据
interface CommonResultListStockIndicatorDailyResponse {
  result?: number;
  message?: string;
  data?: StockIndicatorDailyResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 股票技术指标日快照响应
interface StockIndicatorDailyResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  tradeDate?: string; // 交易日期（格式 YYYY-MM-DD）
  ma5?: number; // 5 日均线
  ma10?: number; // 10 日均线
  ma20?: number; // 20 日均线
  macd?: number; // MACD 值（DIF = EMA12 - EMA26）
  macdSignal?: number; // 信号线（DEA = EMA9 of DIF）
  macdHistogram?: number; // 柱状值（DIF - DEA），正值看多
  rsi?: number; // RSI 14 日强弱指数，>70 超买，<30 超卖
  bollUpper?: number; // 布林上轨（中轨 + 2σ）
  bollMiddle?: number; // 布林中轨（20 日均线）
  bollLower?: number; // 布林下轨（中轨 - 2σ）
  volatility20d?: number; // 20 日收益率标准差（日波动率，非年化）
  kdjK?: number; // KDJ 指标 K 值（0-100），上穿 D 视为短线买点
  kdjD?: number; // KDJ 指标 D 值（0-100），D 线慢于 K 线
  kdjJ?: number; // KDJ 指标 J 值（3K-2D），>100 超买，<0 超卖
  atr14?: number; // 14 日真实波幅 ATR，量化止损距离参考
  volumeRatio?: number; // 量比 = 当日成交量 / 前 5 日均量，>2 视为放量
  macdHistDiff?: number; // MACD 柱状差分（hist - prev_hist），用于识别拐点
  pePercentile5y?: number; // PE 近 5 年历史分位数（0-1），越低越便宜
  pbPercentile5y?: number; // PB 近 5 年历史分位数（0-1），越低越便宜
  beta60d?: number; // 近 60 日 Beta（相对沪深 300），>1 高弹性
  beta120d?: number; // 近 120 日 Beta（相对沪深 300），更稳定的长期 Beta
}

// CommonResultListStockFundFlowResponse 响应数据
interface CommonResultListStockFundFlowResponse {
  result?: number;
  message?: string;
  data?: StockFundFlowResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 股票资金流日快照响应
interface StockFundFlowResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  tradeDate?: string; // 交易日期
  mainInflow?: number; // 主力净流入金额
  mainInflowRatio?: number; // 主力净流入占比
  superLargeInflow?: number; // 超大单净流入（原 largeInflow 重命名，2026-05-20）
  largeInflow?: number; // 真大单净流入（2026-05-20 新增）
  mediumInflow?: number; // 中单净流入
  smallInflow?: number; // 小单净流入
  retailInflow?: number; // 散户净流入
  fundFlowSignal?: string; // 资金流信号（INFLOW / OUTFLOW）
}

// 股票资金流历史查询请求
interface FundFlowHistoryRequest {
  stockCode: string; // 股票代码，如 000001
  days?: number; // 查询最近多少天，默认 60，最大 120
}

// CommonResultFundFlowHistoryResponse 响应数据
interface CommonResultFundFlowHistoryResponse {
  result?: number;
  message?: string;
  data?: FundFlowHistoryResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 股票资金流历史响应
interface FundFlowHistoryResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  points?: FundFlowPoint[]; // 按 trade_date ASC 升序排列的资金流序列
  summary?: FundFlowSummary;
}

// 资金流单日点位
interface FundFlowPoint {
  tradeDate?: string; // 交易日期 YYYY-MM-DD
  mainInflow?: number; // 主力净流入额（元）
  mainInflowRatio?: number; // 主力净流入占比 %
  superLargeInflow?: number; // 超大单净流入（原 largeInflow 重命名，2026-05-20）
  largeInflow?: number; // 真大单净流入（2026-05-20 新增）
  mediumInflow?: number; // 中单净流入
  smallInflow?: number; // 小单净流入
  retailInflow?: number; // 散户净流入
  cumulativeNetInflow?: number; // 截至当日的累计净流入（前缀和）
  fundFlowSignal?: string; // 单日资金流信号：INFLOW / OUTFLOW / NEUTRAL
}

// 资金流区间汇总
interface FundFlowSummary {
  totalDays?: number; // 实际返回的交易日总数
  inflowDays?: number; // 净流入天数
  outflowDays?: number; // 净流出天数
  cumulativeNetInflow?: number; // 区间累计净流入额（元）
  avgDailyInflow?: number; // 日均净流入额（元）
  direction?: string; // 主力近期方向：ACCUMULATING（建仓） / NEUTRAL（中性） / DISTRIBUTING（出货）
}

// CommonResultListStockFinancialResponse 响应数据
interface CommonResultListStockFinancialResponse {
  result?: number;
  message?: string;
  data?: StockFinancialResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 股票财务快照响应（季报维度）
interface StockFinancialResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  reportDate?: string; // 财报期末日（如 2024-09-30）
  roe?: number; // 净资产收益率（%）
  roa?: number; // 总资产收益率（%）
  grossMargin?: number; // 销售毛利率（%）
  debtRatio?: number; // 资产负债率（%）
  netProfitGrowth?: number; // 净利润同比增速（%）
  revenueGrowth?: number; // 营业收入同比增速（%）
  currentRatio?: number; // 流动比率（流动资产/流动负债），>2 视为偿债能力强
  quickRatio?: number; // 速动比率（剔除存货后），>1 视为短期偿债稳健
  cfoToRevenue?: number; // 经营现金流/营业收入（%），衡量利润含金量
  inventoryTurnover?: number; // 存货周转率（次/年），衡量运营效率
}

// 业绩预告查询请求；stockCode 与 reportPeriod 至少传其一
interface EarningsForecastQueryRequest {
  stockCode?: string; // 股票代码（与 reportPeriod 二选一）
  reportPeriod?: string; // 报告期末日 yyyy-MM-dd（与 stockCode 二选一）
  limit?: number; // 返回条数上限，1-200，默认 20
}

// CommonResultListEarningsForecastResponse 响应数据
interface CommonResultListEarningsForecastResponse {
  result?: number;
  message?: string;
  data?: EarningsForecastResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 业绩预告 / 业绩快报响应
interface EarningsForecastResponse {
  id?: number; // 记录 ID
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  noticeDate?: string; // 披露日期（公告日）yyyy-MM-dd
  reportPeriod?: string; // 报告期末日 yyyy-MM-dd（如 2025-09-30 即三季报）
  forecastType?: string; // 预告类型枚举值（INCREASE/DECREASE/TURN_PROFIT/CONTINUE_PROFIT/CONTINUE_LOSS/UNCERTAIN）
  netProfitMin?: number; // 净利润预测下限（元）
  netProfitMax?: number; // 净利润预测上限（元）
  netProfitChangeMin?: number; // 净利同比变动下限（%）
  netProfitChangeMax?: number; // 净利同比变动上限（%）
  summary?: string; // 业绩变动原因摘要
  dataSource?: string; // 数据源
}

// 分析规则信号
interface AnalysisSignalDTO {
  ruleCode?: string; // 规则编码
  ruleVersion?: string; // 规则版本
  signal?: string; // 单条规则输出的信号
  confidence?: number; // 规则信号置信度
  scoreContribution?: number; // 该规则对最终评分的贡献值
  explanation?: string; // 规则解释
  parameterSnapshot?: Record<string, Record<string, any>>; // 规则参数快照
}

// CommonResultStockDetailResponse 响应数据
interface CommonResultStockDetailResponse {
  result?: number;
  message?: string;
  data?: StockDetailResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 单规则信号贡献度明细
interface SignalContributionDTO {
  ruleCode?: string; // 规则码，与 RuleCodeEnum 对齐
  scoreContribution?: number; // 该规则对综合评分的贡献量（正值=加分，负值=减分）
  weight?: number; // 该规则在融合层的权重（0-1）
  signal?: string; // 该规则产生的原始信号：BUY / HOLD / SELL 等
}

// 股票分析响应
interface StockAnalysisResponse {
  stockCode?: string; // 股票代码
  analysisDate?: string; // 分析日期
  analysisType?: string; // 分析类型
  score?: number; // 综合评分
  signal?: string; // 综合信号
  confidence?: number; // 综合置信度
  riskLevel?: string; // 风险等级
  summary?: string; // 分析摘要
  signals?: AnalysisSignalDTO[]; // 规则信号明细
  metrics?: Record<string, Record<string, any>>; // 指标和分析上下文快照
  contributions?: SignalContributionDTO[]; // 规则贡献度明细列表；仅 top 200 推荐有值，其余为 null，前端见 null 不展示贡献度图
}

// 股票详情响应
interface StockDetailResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称
  market?: string; // 所属市场
  industry?: string; // 所属行业
  listDate?: string; // 上市日期
  status?: string; // 股票状态
  marketCap?: number; // 总市值（元），来自 stock_info 流动性快照
  circMarketCap?: number; // 流通市值（元），来自 stock_info 流动性快照
  avgAmount60d?: number; // 60 日均成交额（元），用于流动性判断
  latestAnalysis?: StockAnalysisResponse;
}

// CommonResultStockDetailFullResponse 响应数据
interface CommonResultStockDetailFullResponse {
  result?: number;
  message?: string;
  data?: StockDetailFullResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 行业景气度评分响应
interface IndustryScoreResponse {
  id?: number; // 主键 ID
  industry?: string; // 行业名称
  scoreDate?: string; // 评分日
  avgRoe?: number; // 行业平均 ROE（%）
  avgNetProfitGrowth?: number; // 行业平均净利同比（%）
  avgChangePct?: number; // 行业平均日涨跌幅（%）
  mainInflowTotal?: number; // 行业主力净流入合计（元）
  stockCount?: number; // 入榜股票数
  score?: number; // 综合景气度评分 [0, 100]
  rank?: number; // 当日行业排名（1=最强）
  dataSource?: string; // 数据来源标识
  scoreChange5d?: number; // 近 5 日评分变化量（正值=上行，负值=下行）
  rankChange5d?: number; // 近 5 日排名变化（负值=排名上升/更强）
  rotationStatus?: string; // 轮动状态：INFLOW / OUTFLOW / STABLE
}

// 龙虎榜事件响应（单条上榜记录）
interface LhbEventResponse {
  id?: number; // 记录 ID
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  tradeDate?: string; // 交易日期 yyyy-MM-dd
  reason?: string; // 上榜原因，如「日跌幅偏离值达7%」
  buyAmount?: number; // 龙虎榜买方席位合计买入额（元）
  sellAmount?: number; // 龙虎榜卖方席位合计卖出额（元）
  netAmount?: number; // 净买入额（元），正值表示买方主导
  institutionBuyCount?: number; // 机构席位上榜次数（买方）
  institutionSellCount?: number; // 机构席位上榜次数（卖方）
  hasInstitution?: boolean; // 是否含机构席位（任一方），true 视为机构关注事件
  dataSource?: string; // 数据源
}

// 股票详情一站式聚合响应（11 个维度）
interface StockDetailFullResponse {
  basic?: StockDetailResponse;
  latestQuote?: StockQuoteDailyResponse;
  latestIndicator?: StockIndicatorDailyResponse;
  latestValuation?: StockValuationResponse;
  latestFundFlow?: StockFundFlowResponse;
  latestFinancial?: StockFinancialResponse;
  latestNorthBound?: NorthBoundHoldingResponse;
  recentLhb?: LhbEventResponse[]; // 最近 5 条龙虎榜事件（按交易日降序）
  latestEarnings?: EarningsForecastResponse;
  industryScore?: IndustryScoreResponse;
  latestAnalysis?: StockAnalysisResponse;
  latestIntradaySession?: StockIntradaySessionResponse;
}

// 筹码分布查询请求
interface ChipsDistributionRequest {
  stockCode: string; // 股票代码，如 000001
  lookback?: number; // 回看天数（默认 90，最大 250）
  bins?: number; // 价格区间分桶数（默认 50，范围 20~100）
}

// 筹码价位分桶
interface ChipsBin {
  price?: number; // 价格区间中心价
  weightPct?: number; // 该价位筹码占比（百分比，0~100）
}

// 筹码分布响应
interface ChipsDistributionResponse {
  stockCode?: string; // 股票代码
  tradeDate?: string; // 最新收盘日
  currentPrice?: number; // 当前价（最新收盘价）
  avgCost?: number; // 加权平均持仓成本
  profitRatio?: number; // 获利筹码占比（%），当前价位下方筹码占总筹码比例
  concentration70?: number; // 70% 筹码集中度（占当前价百分比，越小越集中）
  supportPrice?: number; // 支撑位（当前价下方权重最高的成本带）
  pressurePrice?: number; // 压力位（当前价上方权重最高的成本带）
  bins?: ChipsBin[]; // 筹码分桶列表，按价格升序
}

// CommonResultChipsDistributionResponse 响应数据
interface CommonResultChipsDistributionResponse {
  result?: number;
  message?: string;
  data?: ChipsDistributionResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// CommonResultListAnalysisSignalDTO 数据传输对象
interface CommonResultListAnalysisSignalDTO {
  result?: number;
  message?: string;
  data?: AnalysisSignalDTO[];
  errors?: ApiError[];
  ok?: boolean;
}

// 股票分析结果分页请求
interface StockAnalysisPageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  stockCode?: string; // 股票代码
  analysisType?: string; // 分析类型，例如 COMPREHENSIVE
  startDate?: string; // 分析开始日期，格式 yyyy-MM-dd
  endDate?: string; // 分析结束日期，格式 yyyy-MM-dd
}

// PageResultStockAnalysisResponse 响应数据
interface PageResultStockAnalysisResponse {
  result?: number;
  message?: string;
  data?: StockAnalysisResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// CommonResultStockAnalysisResponse 响应数据
interface CommonResultStockAnalysisResponse {
  result?: number;
  message?: string;
  data?: StockAnalysisResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 影子规则 IC 趋势 + 胜率查询请求
interface ShadowRuleIcRequest {
  startDate?: string; // 起始日期，可选；为空时不过滤
  endDate?: string; // 结束日期，可选；为空时不过滤
  limit?: number; // IC 点数返回上限，默认 30，最大 365
}

// CommonResultShadowRuleIcResponse 响应数据
interface CommonResultShadowRuleIcResponse {
  result?: number;
  message?: string;
  data?: ShadowRuleIcResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 影子规则 IC 趋势点
interface RuleIcPoint {
  date?: string; // 统计日期
  ruleCode?: string; // 影子规则码
  ic?: number; // rolling_ic_60d，可为负
}

// 影子规则最新胜率
interface RuleWinRate {
  ruleCode?: string; // 影子规则码
  shadowWinRate?: number; // 影子胜率，0~1
  sampleSize?: number; // 样本数（最新 stats 的 cumulative_closed）
}

// 影子规则 IC 趋势点 + 各规则最新胜率
interface ShadowRuleIcResponse {
  icPoints?: RuleIcPoint[]; // IC 点列表（按 date ASC + ruleCode ASC 排序）
  winRates?: RuleWinRate[]; // 各规则最新累计胜率
}

// 影子规则 vs 实盘累计 PnL 曲线对比请求
interface ShadowPnLCurveRequest {
  startDate?: string; // 起始日期（含），可选；为空时从最早日期开始
  endDate?: string; // 结束日期（含），可选；为空时到今天
  limit?: number; // 曲线点数返回上限，默认 90，最大 365
}

// CommonResultShadowPnLCurveResponse 响应数据
interface CommonResultShadowPnLCurveResponse {
  result?: number;
  message?: string;
  data?: ShadowPnLCurveResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 累计 PnL 对比点
interface PnLCurvePoint {
  statsDate?: string; // 统计日期
  shadowCumPnl?: number; // 影子累计 PnL，小数形式（0.05 = 5%）；当日无影子样本则与前日持平
  liveCumPnl?: number; // 实盘累计 PnL，小数形式（0.05 = 5%）；当日无 CLOSED 样本则与前日持平
  shadowSampleCount?: number; // 影子线当日样本数（参与计算的累计 CLOSED）
  liveSampleCount?: number; // 实盘线当日 CLOSED 样本数（非影子）
}

// 影子规则 vs 实盘累计 PnL 曲线对比响应
interface ShadowPnLCurveResponse {
  points?: PnLCurvePoint[]; // 曲线点列表，按 statsDate ASC 排列；样本不足时返回空数组
}

// CommonResultShadowOverviewResponse 响应数据
interface CommonResultShadowOverviewResponse {
  result?: number;
  message?: string;
  data?: ShadowOverviewResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 影子推荐对比看板概览（影子 vs 实盘的总量 / IC / 胜率）
interface ShadowOverviewResponse {
  shadowCount?: number; // 影子推荐总记录数（shadow_mode=TRUE）
  realCount?: number; // 实盘推荐总记录数（shadow_mode=FALSE）
  shadowAvgIc?: number; // 影子规则最新 stats_date 快照的 rolling_ic_60d 平均值，0~1（无样本时为 null）
  shadowWinRate?: number; // 影子整体胜率（最新 stats_date 各规则 win_rate 按 cumulative_closed 加权平均），0~1
  realWinRate?: number; // 实盘胜率（来自 stock_recommendation_stats_daily 最新一条 win_rate），0~1
}

// 按分析日查询影子/实盘 TOP 10 推荐请求
interface ShadowByDateRequest {
  analysisDate: string; // 分析日期
}

// CommonResultShadowByDateResponse 响应数据
interface CommonResultShadowByDateResponse {
  result?: number;
  message?: string;
  data?: ShadowByDateResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 推荐展示项
interface RecItem {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称
  score?: number; // 推荐评分
  signal?: string; // 推荐信号，对齐 AnalysisSignalEnum
  riskLevel?: string; // 风险等级，对齐 RiskLevelEnum
  rankOrder?: number; // 推荐排序，1 为最高
  suggestedPositionRatio?: number; // 建议仓位比例，小数形式（0.10 = 10%）
  ruleCode?: string; // 命中规则码
}

// 按分析日的影子 / 实盘 TOP 10 推荐对比
interface ShadowByDateResponse {
  shadowRecs?: RecItem[]; // 影子推荐 TOP 10（shadow_mode=TRUE，按 rank_order 升序）
  realRecs?: RecItem[]; // 实盘推荐 TOP 10（shadow_mode=FALSE，按 rank_order 升序）
}

// 样本门控进度看板请求（可选 mode 分桶）
interface SampleProgressOverviewRequest {
  positionSizingMode?: string; // 按仓位算法 mode 过滤：formula / fixed；null 表示不过滤
}

// CommonResultSampleProgressOverviewResponse 响应数据
interface CommonResultSampleProgressOverviewResponse {
  result?: number;
  message?: string;
  data?: SampleProgressOverviewResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 样本门控进度看板响应（CLOSED 严格定义 + Wilson 区间 + 阶段判定）
interface SampleProgressOverviewResponse {
  sampleStartDate?: string; // 样本起点（CLOSED 严格定义生效的最早 recommend_date）
  asOfDate?: string; // 查询时间戳（服务端日期）
  closedCount?: number; // CLOSED 严格定义下的样本数（status=CLOSED 且 recommend_date >= SAMPLE_START_DATE）
  trackingCount?: number; // TRACKING 在途样本数（仅展示，不计入门控）
  winCount?: number; // CLOSED 中盈利数（actual_return > 0）
  winRate?: number; // 胜率（小数形式，0.6190 = 61.9%）；closedCount=0 时为空
  wilsonLower?: number; // Wilson Score 95% 置信区间下界；closedCount=0 时为空
  wilsonUpper?: number; // Wilson Score 95% 置信区间上界；closedCount=0 时为空
  wilsonWidth?: number; // Wilson 区间宽度 upper-lower；closedCount=0 时为空
  exitReasonEntropy?: number; // exit_reason 香农熵（bits），单一原因为 0，三类均匀约 1.585；closedCount=0 时为空
  exitReasonDistribution?: Record<string, number>; // exit_reason 分布 {STOP_LOSS: n, TAKE_PROFIT: n, HOLD_PERIOD: n, ...}
  track4Threshold?: number; // Track 4 解锁门槛（固定 50）
  track5Threshold?: number; // Track 5 解锁门槛（固定 100）
  track4Remaining?: number; // 距离 Track 4 解锁还差多少 CLOSED 样本，已解锁时为 0
  track5Remaining?: number; // 距离 Track 5 解锁还差多少 CLOSED 样本，已解锁时为 0
  currentPhase?: string; // 当前阶段：'Track 1-3' / 'Track 4 ready' / 'Track 5 ready'
  unlockBlockers?: string[]; // 解锁 Track 4/5 的阻塞原因列表（空数组表示该档已具备解锁条件）
  positionSizingMode?: string; // 本次统计的 mode 过滤值：formula / fixed / null（含全部）；用于前端展示当前分桶口径
}

// 样本门控进度历史查询请求（按日返回 CLOSED 累计 / 胜率 / Wilson 区间）
interface SampleProgressHistoryRequest {
  startDate?: string; // 起始日期（包含），null 表示不过滤
  endDate?: string; // 结束日期（包含），null 表示不过滤
  limit?: number; // 返回最多多少天的快照，默认 90，最大 365
}

// CommonResultSampleProgressHistoryResponse 响应数据
interface CommonResultSampleProgressHistoryResponse {
  result?: number;
  message?: string;
  data?: SampleProgressHistoryResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 样本门控进度历史每日点（CLOSED 累计 + 胜率 + Wilson 区间）
interface SampleProgressDailyPoint {
  date?: string; // 统计日期
  closedCount?: number; // 当日累计 CLOSED 样本数
  winRate?: number; // 胜率（小数形式，0.6190 = 61.9%）；CLOSED=0 时为空
  wilsonLower?: number; // Wilson Score 95% 置信区间下界；CLOSED=0 时为空
  wilsonUpper?: number; // Wilson Score 95% 置信区间上界；CLOSED=0 时为空
}

// 样本门控进度历史响应（按 date 升序的每日点列表）
interface SampleProgressHistoryResponse {
  points?: SampleProgressDailyPoint[]; // 每日快照点列表（按 date 升序）
}

// 创建/更新角色请求
interface RoleSaveRequest {
  id?: number; // 角色 ID（新建时不传）
  code: string; // 角色唯一标识符（大写，如 ANALYST）
  name: string; // 角色显示名称
  description?: string; // 角色说明
  sort?: number; // 排序值（升序）
}

// 角色分页查询请求
interface RolePageRequest {
  page?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  keyword?: string; // 关键字，用于角色名称/编码模糊查询
}

// PageResultRoleResponse 响应数据
interface PageResultRoleResponse {
  result?: number;
  message?: string;
  data?: RoleResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 角色信息
interface RoleResponse {
  id?: number; // 角色 ID
  code?: string; // 角色唯一标识符
  name?: string; // 角色显示名称
  description?: string; // 角色说明
  isBuiltIn?: boolean; // 是否内置角色（内置角色不可删除）
  sort?: number; // 排序值
  status?: string; // 状态：ACTIVE / DISABLED
}

// 查询角色已关联菜单请求
interface RoleMenusRequest {
  roleId: number; // 角色主键 ID
}

// CommonResultListLong 接口
interface CommonResultListLong {
  result?: number;
  message?: string;
  data?: number[];
  errors?: ApiError[];
  ok?: boolean;
}

// CommonResultListRoleResponse 响应数据
interface CommonResultListRoleResponse {
  result?: number;
  message?: string;
  data?: RoleResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 角色删除请求
interface RoleDeleteRequest {
  id: number; // 角色主键 ID
}

// 为角色分配菜单/权限请求
interface AssignMenusRequest {
  roleId: number; // 目标角色 ID
  menuIds?: number[]; // 菜单 ID 列表（空列表表示清空所有菜单权限）
}

// 推荐归因报告查询请求
interface AttributionReportRequest {
  date?: string; // 报告日期（yyyy-MM-dd）；为空时取最新一期
}

// 推荐归因报告响应
interface AttributionReportResponse {
  reportDate?: string; // 报告日期
  title?: string; // 报告标题
  recommendationCount?: number; // 推荐总数
  signalDistribution?: Record<string, Record<string, any>>; // 信号分布，key=信号，value=数量
  topRuleContributions?: Record<string, Record<string, any>>[]; // 贡献最大规则 Top N，元素含 ruleCode / hitCount / avgScore 等
  industryDistribution?: Record<string, Record<string, any>>; // 行业分布，key=行业，value=数量
  riskDistribution?: Record<string, Record<string, any>>; // 风险等级分布，key=等级，value=数量
  diffVsPrevious?: Record<string, Record<string, any>>; // 与上一期的差异概览
  snapshotSourceMap?: Record<string, string>; // 推荐 ID → 数据来源标记的映射（snapshot=入选时冻结快照，recomputed=重算）
  snapshotSourceSummary?: SnapshotSourceSummary;
}

// CommonResultAttributionReportResponse 响应数据
interface CommonResultAttributionReportResponse {
  result?: number;
  message?: string;
  data?: AttributionReportResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 快照来源汇总
interface SnapshotSourceSummary {
  snapshot?: number; // snapshot 类型推荐条数（入选时冻结的字段快照）
  recomputed?: number; // recomputed 类型推荐条数（基于最新指标重算）
}

// 推荐入选股票实盘追踪查询请求
interface RecommendationTrackQueryRequest {
  days?: number; // 回溯天数（最近 N 天）
  status?: string; // 状态过滤：TRACKING / CLOSED；为空则不过滤
  policyVersion?: string; // 按 policy_version 过滤（如 p1.14-morning / p1.14-eod / p1.13-north-contaminated），null 不过滤
  positionSizingMode?: string; // 按仓位算法 mode 过滤：formula / fixed；null 表示不过滤（含全部 mode）
}

// CommonResultListRecommendationTrackResponse 响应数据
interface CommonResultListRecommendationTrackResponse {
  result?: number;
  message?: string;
  data?: RecommendationTrackResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 推荐入选股票实盘追踪响应
interface RecommendationTrackResponse {
  id?: number; // 主键 ID
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称，stock_info 未维护时为空
  recommendDate?: string; // 推荐入选日（建仓日）
  signal?: string; // 入选时信号：BUY / STRONG_BUY / SELL 等
  score?: number; // 入选时规则引擎评分
  entryPrice?: number; // 入选日收盘价（建仓基准价）
  checkDate?: string; // 跟踪检查日；TRACKING 状态可能为空
  daysHeld?: number; // 持有天数
  exitPrice?: number; // 检查日收盘价（平仓价）
  currentPrice?: number; // 最新交易日收盘价，TRACKING 状态用于展示当前价
  currentTradeDate?: string; // 最新交易日，TRACKING 状态用于展示当前价日期
  unrealizedReturn?: number; // 未实现收益率，仅 TRACKING 状态计算（小数表示，0.05 = 5%）
  actualReturn?: number; // 实际收益率（小数表示，0.05 = 5%）
  executionCapital?: number; // 执行金额（建仓时入场金额，来自 stock_trade_plan.execution_capital 或 stock_recommend_result.suggested_amount）
  pnlAmount?: number; // 总盈亏金额（CLOSED: actual_return × execution_capital；TRACKING: (currentPrice - entryPrice) / entryPrice × execution_capital；任一为 null 时返回 null）
  isWin?: boolean; // 是否盈利；TRACKING 状态可能为空
  status?: string; // 状态：TRACKING（跟踪中）/ CLOSED（已统计）
  peakPrice?: number; // 持仓期间最高价
  peakDate?: string; // 最高价出现日期
  troughPrice?: number; // 持仓期间最低价
  troughDate?: string; // 最低价出现日期
  stopTriggeredDate?: string; // 止损触发日期；未触发时为 null
  takeProfitDate?: string; // 止盈触发日期；未触发时为 null
  exitReason?: string; // 平仓原因：HOLD_PERIOD / STOP_LOSS / TAKE_PROFIT / MANUAL
  maxDrawdown?: number; // 持仓期间最大回撤（小数，负值）
  holdingPeriodMin?: number; // 策略要求最短持仓天数
  holdingPeriodMax?: number; // 策略要求最长持仓天数
  stopLossPct?: number; // 实际使用的止损比例（小数）
  takeProfitPct?: number; // 实际使用的止盈比例（小数）
  stopLossPrice?: number; // 止损价（entry_price × (1 + stopLossPct)），entry_price 或 pct 为 null 时返回 null
  takeProfitPrice?: number; // 止盈价（entry_price × (1 + takeProfitPct)），entry_price 或 pct 为 null 时返回 null
  policyVersion?: string; // 策略版本号 / 污染标签，含 'north-contaminated' 表示已 deprecated 数据源
  positionSizingMode?: string; // 仓位算法 mode: formula / fixed / kelly，源自推荐时刻冻结值
  trackingSnapshotAtExit?: string; // 平仓时冻结的规则信号 / 衰减 / 市场环境 / ATR JSON 快照
  exitReasonCategory?: string; // 退出原因分类：STOP_LOSS / TAKE_PROFIT / TIME_EXIT / SIGNAL_REVERSAL / MAX_DRAWDOWN
  intradayLowBreachedStop?: boolean; // 盘中最低价是否触及/跌破止损价（诊断字段）
  intradayHighHitProfit?: boolean; // 盘中最高价是否触及/达到止盈价（诊断字段）
  intradayDiagnosticUpdatedAt?: string; // 盘中诊断字段最近更新时间
}

// 推荐追踪批量补登记请求
interface RecommendationTrackBootstrapRequest {
  date?: string; // 推荐日期，格式 yyyy-MM-dd；为空时取系统当天
}

// CommonResultMapStringObject 接口
interface CommonResultMapStringObject {
  result?: number;
  message?: string;
  data?: Record<string, Record<string, any>>;
  errors?: ApiError[];
  ok?: boolean;
}

// 推荐追踪详情查询请求
interface RecommendationTrackDetailRequest {
  trackId: number; // 推荐追踪记录主键 ID
}

// CommonResultRecommendationTrackDetailResponse 响应数据
interface CommonResultRecommendationTrackDetailResponse {
  result?: number;
  message?: string;
  data?: RecommendationTrackDetailResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 推荐追踪详情聚合响应
interface RecommendationTrackDetailResponse {
  track?: RecommendationTrackResponse;
  quotes?: Record<string, Record<string, any>>[]; // 持有期间每日行情序列（按 trade_date 升序）
}

// CommonResultRecommendationPerformanceResponse 响应数据
interface CommonResultRecommendationPerformanceResponse {
  result?: number;
  message?: string;
  data?: RecommendationPerformanceResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 推荐胜率与平均收益汇总响应
interface RecommendationPerformanceResponse {
  days?: number; // 回溯天数
  total?: number; // 已 CLOSED 总条数
  wins?: number; // 盈利条数
  losses?: number; // 亏损条数
  winRate?: number; // 胜率（百分数，65.00 = 65%）；样本不足时为空
  avgReturn?: number; // 平均收益率（百分数）；样本不足时为空
  avgWinReturn?: number; // 盈利样本平均收益率（百分数）
  avgLossReturn?: number; // 亏损样本平均收益率（百分数，负值）
  stopLossRate?: number; // 触发止损平仓比例（百分数，10.00 = 10%）；样本不足时为空
  takeProfitRate?: number; // 触发止盈平仓比例（百分数）；样本不足时为空
  holdPeriodWinRate?: number; // 持有期满正常平仓比例（百分数）；样本不足时为空
  avgMaxDrawdown?: number; // 平均最大回撤（百分数，负值）；样本不足时为空
}

// CommonResultRecommendationPolicyBreakdownResponse 响应数据
interface CommonResultRecommendationPolicyBreakdownResponse {
  result?: number;
  message?: string;
  data?: RecommendationPolicyBreakdownResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// policy_version 维度下的推荐胜率聚合项
interface RecommendationPolicyBreakdownItem {
  policyVersion?: string; // 策略版本标签（'(unlabeled)' 表示 NULL 历史行 / 早期僵尸样本）
  total?: number; // 该 policy 下 CLOSED 总条数
  wins?: number; // 盈利条数
  losses?: number; // 亏损条数
  winRate?: number; // 胜率（百分数，65.00 = 65%）；样本不足时为空
  avgReturn?: number; // 平均收益率（百分数）；样本不足时为空
  avgWinReturn?: number; // 盈利样本平均收益率（百分数）
  avgLossReturn?: number; // 亏损样本平均收益率（百分数，负值）
  stopLossRate?: number; // 触发止损平仓比例（百分数）
  takeProfitRate?: number; // 触发止盈平仓比例（百分数）
  firstRecommendDate?: string; // 该 policy 最早 recommend_date
  lastRecommendDate?: string; // 该 policy 最新 recommend_date
}

// 按 policy_version 分桶的 CLOSED 推荐胜率聚合响应
interface RecommendationPolicyBreakdownResponse {
  days?: number; // 回溯天数
  positionSizingMode?: string; // 过滤的仓位算法 mode（null 表示全部）
  items?: RecommendationPolicyBreakdownItem[]; // policy_version 维度聚合项（按 total 倒序）
}

// 创建/更新菜单请求
interface MenuSaveRequest {
  id?: number; // 菜单 ID（新建时不传）
  parentId?: number; // 父节点 ID（null 表示顶级节点）
  name: string; // 菜单名称
  type: string; // 类型：CATALOG / MENU / BUTTON
  path?: string; // 路由路径（BUTTON 类型为 null）
  component?: string; // 前端组件路径（BUTTON/CATALOG 为 null）
  icon?: string; // 图标名称（Ant Design Icons）
  permissionCode?: string; // 权限码（CATALOG 为 null）
  sort?: number; // 排序值（升序）
  visible?: boolean; // 是否在菜单中显示（BUTTON 节点为 false）
}

// CommonResultListMenuTreeResponse 响应数据
interface CommonResultListMenuTreeResponse {
  result?: number;
  message?: string;
  data?: MenuTreeResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 菜单树节点
interface MenuTreeResponse {
  id?: number; // 菜单 ID
  parentId?: number; // 父节点 ID
  name?: string; // 菜单名称
  type?: string; // 类型：CATALOG / MENU / BUTTON
  path?: string; // 路由路径
  component?: string; // 前端组件路径
  icon?: string; // 图标名称
  permissionCode?: string; // 权限码
  sort?: number; // 排序值
  visible?: boolean; // 是否可见
  status?: string; // 状态
  children?: MenuTreeResponse[]; // 子节点列表（递归）
}

// 菜单删除请求
interface MenuDeleteRequest {
  id: number; // 菜单主键 ID
}

// CommonResultListMarketIndexLatestResponse 响应数据
interface CommonResultListMarketIndexLatestResponse {
  result?: number;
  message?: string;
  data?: MarketIndexLatestResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 大盘指数最新行情
interface MarketIndexLatestResponse {
  indexCode?: string; // 指数代码，如 sh000300
  indexName?: string; // 指数中文名，如 沪深300
  tradeDate?: string; // 交易日
  closePrice?: number; // 收盘点位
  changePct?: number; // 涨跌幅（%）
  openPrice?: number; // 开盘点位
  highPrice?: number; // 最高点位
  lowPrice?: number; // 最低点位
  prevClose?: number; // 昨日收盘点位
  volume?: number; // 成交量（手）
  amount?: number; // 成交额（元）
  dataSource?: string; // 数据源，如 AKSHARE / EASTMONEY
}

// 大盘指数历史走势查询请求
interface MarketIndexHistoryRequest {
  indexCode: string; // 指数代码，如 sh000300
  days?: number; // 查询最近 N 天，默认 60；最大 365
}

// 龙虎榜按股票查询请求
interface LhbQueryByStockRequest {
  stockCode: string; // 股票代码
  days?: number; // 向前查询的自然日数，1-365，默认 90
}

// CommonResultListLhbEventResponse 响应数据
interface CommonResultListLhbEventResponse {
  result?: number;
  message?: string;
  data?: LhbEventResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 龙虎榜按日期查询请求
interface LhbQueryByDateRequest {
  tradeDate: string; // 交易日期 yyyy-MM-dd
}

// 行业轮动趋势查询请求
interface IndustryRotationTrendRequest {
  industry: string; // 行业名称（与 stock_info.industry 对齐）
  days?: number; // 回溯天数，默认 30 日
}

// CommonResultListIndustryRotationTrendResponse 响应数据
interface CommonResultListIndustryRotationTrendResponse {
  result?: number;
  message?: string;
  data?: IndustryRotationTrendResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 单行业轮动趋势序列响应
interface IndustryRotationTrendResponse {
  scoreDate?: string; // 评分日（格式 YYYY-MM-DD）
  rank?: number; // 当日行业排名（1=最强）
  scoreChange5d?: number; // 近 5 日评分变化量（正值=上行，负值=下行）
  rotationStatus?: string; // 轮动状态：INFLOW / OUTFLOW / STABLE
}

// 行业景气度排名查询请求
interface IndustryRankingQueryRequest {
  limit?: number; // 返回条数，默认 20，最多 100
}

// CommonResultListIndustryScoreResponse 响应数据
interface CommonResultListIndustryScoreResponse {
  result?: number;
  message?: string;
  data?: IndustryScoreResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 行业龙头股查询请求
interface IndustryLeadingStocksRequest {
  industry: string; // 行业名称(与 stock_info.industry 对齐)
  limit?: number; // 返回 top N,默认 5 条
}

// CommonResultListIndustryLeadingStockResponse 响应数据
interface CommonResultListIndustryLeadingStockResponse {
  result?: number;
  message?: string;
  data?: IndustryLeadingStockResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 行业龙头股
interface IndustryLeadingStockResponse {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称
  marketCap?: number; // 总市值（元）
  circMarketCap?: number; // 流通市值（元）
  latestPrice?: number; // 最新收盘价
  changePct?: number; // 最新交易日涨跌幅(%)
  latestTradeDate?: string; // 最新交易日日期
}

// 单行业景气度历史查询请求
interface IndustryDetailQueryRequest {
  industry: string; // 行业名称（与 stock_info.industry 对齐）
  days?: number; // 回溯天数，默认 30 天
}

// 4W 草稿转交易计划请求
interface FourWToTradePlanRequest {
  fourWPlanId: number; // 4W 草稿 ID
}

// 4W 投资草稿分页查询请求
interface FourWPageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  stockCode?: string; // 股票代码筛选
  signal?: string; // 信号筛选：BUY / STRONG_BUY
}

// 4W 投资草稿响应
interface FourWPlanResponse {
  id?: number; // 草稿 ID
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称，stock_info 未维护时为空
  planDate?: string; // 草稿日期
  signal?: string; // 信号（BUY / STRONG_BUY）
  score?: number; // 规则引擎评分
  suggestedPositionRatio?: number; // 建议仓位比例
  why?: string; // 买入理由 JSON 字符串
  what?: string; // 动作代码
  whenTrigger?: string; // 触发时机代码
  whatIf?: string; // 风控预案 JSON 字符串
  metadata?: string; // 其他上下文 JSON 字符串
  deploymentRatio?: number; // 当日部署比例，0.0-1.0
  positionAdvice?: string; // 仓位建议：INCREASE/KEEP/DECREASE/SKIP
  stopLossPct?: number; // 止损百分比
  takeProfitPct?: number; // 止盈百分比
  targetPrice?: number; // 目标价（最近收盘价）
  stopLossPrice?: number; // 止损价（自动算）
  takeProfitPrice?: number; // 止盈价（自动算）
  executionCapital?: number; // 执行金额（按当前用户总资金算）
  policyVersion?: string; // 策略版本号，与 RecommendResultResponse.policyVersion 同源（如 p1.14-eod）；旧行 null 表示 legacy
}

// PageResultFourWPlanResponse 响应数据
interface PageResultFourWPlanResponse {
  result?: number;
  message?: string;
  data?: FourWPlanResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 通用主键请求
interface IdRequest {
  id: number; // 数据库主键 ID
}

// CommonResultFourWPlanResponse 响应数据
interface CommonResultFourWPlanResponse {
  result?: number;
  message?: string;
  data?: FourWPlanResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 系统资金调整建议
interface CapitalSuggestion {
  action?: string; // 建议动作：INCREASE / HOLD
  suggestedAmount?: number; // 建议调整后的总资金金额（元）
  reason?: string; // 建议原因说明
}

// 账户资金摘要
interface CapitalSummaryResponse {
  totalCapital?: number; // 账户总资金（元）
  committedCapital?: number; // 已激活占用（ACTIVE 计划 execution_capital 之和）
  availableCapital?: number; // 可用资金 = 总资金 - 已占用，最小 0
  neededCapital?: number; // 最新期 BUY/STRONG_BUY 候选所需资金之和（按 1 手对齐后的可执行金额，资金不足的不计入）
  recommendedTotal?: number; // 策略推荐总金额（DB stock_recommend_result.suggested_amount 之和，含资金不足项；与推荐表格金额列上行同源）
  gap?: number; // 资金缺口 = 所需 - 可用，负数表示有余
  gapStatus?: string; // 资金状态：SUFFICIENT/TIGHT/SHORTAGE/OVER_COMMITTED
  buySignalCount?: number; // 最新期 BUY/STRONG_BUY 信号数量
  buyableCount?: number; // 实际可买入 1 手以上的候选数（lots > 0）
  insufficientCount?: number; // 资金不足以买入 1 手或行情缺失被剔除的候选数（lots = 0）
  latestPlanDate?: string; // 最新 4W 计划日期
  overCommitted?: boolean; // 是否超配（committedCapital > totalCapital）
  reductionAlerts?: ReductionAlert[]; // 减仓关注列表
  capitalSuggestion?: CapitalSuggestion;
}

// CommonResultCapitalSummaryResponse 响应数据
interface CommonResultCapitalSummaryResponse {
  result?: number;
  message?: string;
  data?: CapitalSummaryResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 减仓关注项
interface ReductionAlert {
  stockCode?: string; // 股票代码
  alertType?: string; // 告警类型：SIGNAL_DEGRADED / STOP_LOSS_NEAR / TAKE_PROFIT_HIT
  originalSignal?: string; // 交易计划中的原始信号
  latestSignal?: string; // 最新 4W 信号
  closePrice?: number; // 最新收盘价
  stopLossPrice?: number; // 止损价
  takeProfitPrice?: number; // 止盈价
  executionCapital?: number; // 该计划执行金额
  detail?: string; // 说明文本
}

// CommonResultPerformanceReportResponse 响应数据
interface CommonResultPerformanceReportResponse {
  result?: number;
  message?: string;
  data?: PerformanceReportResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 策略绩效报告响应
interface PerformanceReportResponse {
  reportDate?: string; // 报告日期
  stockCode?: string; // 股票代码，null 表示全组合
  stockName?: string; // 股票名称，组合级绩效为空
  periodDays?: number; // 统计周期（交易日数）
  totalReturn?: number; // 累计收益率
  annualizedReturn?: number; // 年化收益率
  maxDrawdown?: number; // 最大回撤（负值）
  volatility?: number; // 年化波动率
  sharpeRatio?: number; // 夏普比率
  sortinoRatio?: number; // Sortino 比率
  calmarRatio?: number; // Calmar 比率
  winRate?: number; // 胜率
  alpha?: number; // 超额年化收益
  beta?: number; // 市场敏感度
  informationRatio?: number; // 信息比率
}

// 策略诊断分页查询请求
interface DiagnosticsPageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  stockCode?: string; // 股票代码筛选，为空时返回所有股票
}

// PageResultPerformanceReportResponse 响应数据
interface PageResultPerformanceReportResponse {
  result?: number;
  message?: string;
  data?: PerformanceReportResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 因子 IC 诊断响应
interface FactorIcResponse {
  calcDate?: string; // 计算日期
  forwardDays?: number; // 前向收益周期（交易日数）
  ic?: number; // 单期 Spearman IC
  pvalue?: number; // IC 显著性 p 值
  meanIc?: number; // IC 均值
  stdIc?: number; // IC 标准差
  ir?: number; // IR = mean_IC / std_IC
  coverage?: number; // 非 NaN 覆盖率
  healthy?: boolean; // 是否通过健康检查（IC > 0.03 且 IR > 0.5）
  nsamples?: number;
}

// PageResultFactorIcResponse 响应数据
interface PageResultFactorIcResponse {
  result?: number;
  message?: string;
  data?: FactorIcResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// CommonResultFactorIcResponse 响应数据
interface CommonResultFactorIcResponse {
  result?: number;
  message?: string;
  data?: FactorIcResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// CommonResultListDataSourceComplianceResponse 响应数据
interface CommonResultListDataSourceComplianceResponse {
  result?: number;
  message?: string;
  data?: DataSourceComplianceResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 数据源商用合规台账
interface DataSourceComplianceResponse {
  sourceName?: string; // 数据源名称（大写）
  officialUrl?: string; // 官网 / 服务条款 URL；非官方源可能为空
  license?: string; // 授权许可证类型（如 MIT / 商业付费 / 未明示）
  commercialStatus?: string; // 商用合规状态：free / restricted / unknown / paid
  dataAttribution?: string; // 数据归属说明（数据来源 + 二次分发说明）
  pitLag?: string; // PIT 数据延迟说明（影响回测真实性）
  currentUsage?: string; // 当前在系统中的实际使用场景
  riskAssessment?: string; // 风险评估：low / medium / high
  mitigation?: string; // 缓释措施（备援源 / 替换计划 / 合规备注）
}

// 数据健康看板查询请求
interface DataHealthOverviewRequest {
  days?: number; // 跑批连续性回溯天数（同时也是高优告警回溯窗口）
}

// 高优告警摘要
interface AlertSummary {
  id?: number; // 告警 ID
  alertType?: string; // 告警类型（如 PIPELINE_FAILURE / MAX_DRAWDOWN_HIT / RULE_DECAY）
  stockCode?: string; // 关联股票代码；系统级告警时为空
  title?: string; // 告警标题
  severity?: string; // 严重级别（固定 HIGH）
  alertTime?: string; // 告警产生时间（ISO 格式 YYYY-MM-DD HH:mm:ss）
}

// CommonResultDataHealthOverviewResponse 响应数据
interface CommonResultDataHealthOverviewResponse {
  result?: number;
  message?: string;
  data?: DataHealthOverviewResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 单日行情条数
interface DailyQuoteCount {
  tradeDate?: string; // 交易日（ISO 格式 YYYY-MM-DD）
  count?: number; // 当日 stock_quote_daily 行情记录数；缺日时为 0
}

// 数据健康总览响应
interface DataHealthOverviewResponse {
  pipelineContinuity?: DailyQuoteCount[]; // 跑批连续性：最近 N 天 stock_quote_daily 行情条数（按交易日倒序）
  recommendationTrackProgress?: RecommendationTrackProgress;
  dataSourceFreshness?: DataSourceFreshness[]; // 数据源新鲜度：6 张核心表的最新交易日 + 距今天数
  highAlerts?: AlertSummary[]; // 最近 N 天 severity=HIGH 告警列表
}

// 数据源新鲜度项
interface DataSourceFreshness {
  tableName?: string; // 数据表名
  tableLabel?: string; // 中文标签（前端展示友好名）
  latestDate?: string; // 该表最新业务日期（ISO 格式）；无数据时为 null
  daysAgo?: number; // 距今天的天数；无数据时为 null
  stale?: boolean; // 是否警告（>= 阈值未更新或无数据）；阈值按表分级
  staleThresholdDays?: number; // stale 判定阈值（天）：日频 3 / 稀疏日频 7 / 季度披露 120
  coverageCount?: number; // 覆盖股票数（数据源覆盖到的不同 stock_code 数），仅采集层表填充
  quarterCoverageRate?: number; // 财务季报最新报告期覆盖率（0~1），仅 stock_financial_snapshot 填充
  fetcherType?: string; // 数据采集源类型，对应 DataFetcherEnum 枚举值；前端通过 useModel('enum').getFormattedEnums('DataFetcherEnum') 取展示 label
}

// 退出原因分布项
interface ExitReasonCount {
  exitReason?: string; // 退出原因（来自 Python 端枚举：STOP_LOSS/TAKE_PROFIT/HOLDING_TIMEOUT/MANUAL/UNKNOWN 等）
  count?: number; // 该退出原因的 CLOSED 条数
}

// 推荐追踪进度
interface RecommendationTrackProgress {
  trackingTotal?: number; // TRACKING 状态总数（在跟踪中）
  closedTotal?: number; // CLOSED 状态总数（已平仓）
  closedTargetProgress?: number; // 距 60 个 CLOSED 样本目标的进度（0..1）
  closedTarget?: number; // CLOSED 样本目标基线（统计意义所需最少样本数）
  exitReasons?: ExitReasonCount[]; // 退出原因分布：按 exit_reason 聚合的条数（CLOSED）
  winRate?: number; // CLOSED 胜率，小数形式（0..1）；样本不足时为 null
  avgReturn?: number; // CLOSED 平均收益率，小数形式；可正可负
  avgDaysHeld?: number; // CLOSED 平均持有天数；样本不足时为 null
}

// 数据完整性缺口明细查询请求
interface DataIntegrityGapRequest {
  days?: number; // 回溯天数（向前查多少天的行情）
}

// CommonResultDataIntegrityGapResponse 响应数据
interface CommonResultDataIntegrityGapResponse {
  result?: number;
  message?: string;
  data?: DataIntegrityGapResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 数据完整性缺口明细响应
interface DataIntegrityGapResponse {
  totalGapCount?: number; // 总缺口数（按 trade_date + stock_code 去重计数）
  totalAffectedStocks?: number; // 涉及不同股票数（按 stock_code 去重计数）
  byDate?: DateGapDetail[]; // 按交易日分组的缺口明细，按日期倒序
}

// 按日期分组的缺口明细
interface DateGapDetail {
  tradeDate?: string; // 交易日
  gapType?: string; // 缺口类型（如 QUOTE_HAS_INDICATOR_MISSING）
  affectedStocks?: StockGapItem[]; // 受影响的股票明细
}

// 缺口股票条目
interface StockGapItem {
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（缺失时回退展示代码）
  industry?: string; // 所属行业；stock_info 未维护时为 null
}

// CommonResultDashboardSummaryResponse 响应数据
interface CommonResultDashboardSummaryResponse {
  result?: number;
  message?: string;
  data?: DashboardSummaryResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 仪表盘汇总响应
interface DashboardSummaryResponse {
  stockCount?: number; // 股票总数
  watchlistCount?: number; // 关注股票数量
  latestTradeDate?: string; // 最新交易日
  latestSyncStatus?: string; // 最近同步任务状态
  todayRecommendCount?: number; // 今日推荐数（最新分析日推荐结果总条数）
  watchlistAlertCount?: number; // 持仓预警数：关注池中存在未处理告警的股票数
  pendingJobCount?: number; // 待处理任务数：PENDING + RUNNING 同步任务总数
  recentSignalAccuracy?: number; // 最近 7 天信号正确率（0-1 小数，无回验数据时为 null）
  topRecommendations?: RecommendResultResponse[]; // 首页展示的最新推荐结果（最多 10 条）
  riskDistribution?: Record<string, number>; // 风险等级分布，key 为风险等级，value 为数量
  recentRecommendationPerformance?: RecommendationPerformanceResponse;
  topIndustryRanking?: IndustryScoreResponse[]; // 行业景气度排行榜（前 5）
  pausedSignals?: string[]; // 因回撤暂停的信号集合（最近 7 日 MAX_DRAWDOWN_HIT 告警去重）
  decayedRuleCount?: number; // 信号衰减告警数（今日 RULE_DECAY 告警计数）
  marketIndices?: MarketIndexLatestResponse[]; // 5 大指数最新行情（顶部 widget 用）
}

// 推荐结果响应
interface RecommendResultResponse {
  id?: number; // 推荐结果主键 ID
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称
  marketCap?: number; // 总市值（元），来自 stock_info 流动性快照
  circMarketCap?: number; // 流通市值（元），来自 stock_info 流动性快照
  avgAmount60d?: number; // 60 日均成交额（元），用于推荐流动性提示
  analysisDate?: string; // 分析日期
  score?: number; // 推荐评分
  signal?: string; // 推荐信号
  riskLevel?: string; // 风险等级
  suggestedPositionRatio?: number; // 建议仓位比例
  suggestedAmount?: number; // 建议金额；资金未确定时允许为空
  stopLossPct?: number; // 建议止损比例
  takeProfitPct?: number; // 建议止盈比例
  reasons?: string[]; // 推荐理由列表
  positionExplain?: Record<string, Record<string, any>>; // 仓位决策因子分解（5 因子：score/confidence/signal/risk/cost）
  effectiveScore?: number; // 信号衰减后的实际评分（用于排序），与原始 score 区分
  signalAgeDays?: number; // 信号距今天数；超过宽限期视为陈旧
  holdingPeriod?: string; // 建议持股周期描述
  breakevenReturn?: number; // 盈亏平衡所需最低收益率（覆盖佣金 + 印花税 + 滑点）
  costDetail?: Record<string, Record<string, any>>; // 交易成本因子分解（JSON）
  reasonSnapshot?: string; // 推荐理由快照（JSON 字符串），生成时冻结的规则触发明细 + 阈值 + 当时观察值，合规 7 年追溯
  disclaimerSnapshot?: string; // 免责声明快照（JSON 字符串），含 scenario / title / description / version，留痕用户当时看到的文案
  signalSnapshot?: string; // 融合层快照（JSON 字符串），signal_explanation + score_contributions，归因看板数据源
  fourWPlanId?: number; // 4W 计划ID（用于转交易计划）
  deploymentRatio?: number; // 部署比例（市场基调决定）
  positionAdvice?: string; // 仓位建议：INCREASE/KEEP/DECREASE/SKIP
  stopLossPrice?: number; // 止损价（绝对价格）
  takeProfitPrice?: number; // 止盈价（绝对价格）
  targetPrice?: number; // 目标价
  executionCapital?: number; // 执行金额（元），按 A 股 1 手 = 100 股向下取整后口径，与 suggestedLots 一致；行情缺失或资金不足以买 1 手时为 0
  suggestedLots?: number; // 建议买入手数（A 股 1 手 = 100 股），0 表示资金不足以买 1 手或行情缺失；与 executionCapital 同口径，由 LotSizeCalculator 按最新收盘价对齐
  policyVersion?: string; // 策略版本号 / 污染标签，含 'north-contaminated' 表示已 deprecated 数据源
  positionSizingMode?: string; // 仓位算法 mode: formula / fixed / kelly，由 Python 推荐时刻冻结
}

// 仓位算法 mode 切换请求
interface PositionSizingModeRequest {
  mode: string; // 仓位算法 mode，仅允许 formula | fixed
}

// CommonResultPositionSizingModeResponse 响应数据
interface CommonResultPositionSizingModeResponse {
  result?: number;
  message?: string;
  data?: PositionSizingModeResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 仓位算法 mode 查询响应
interface PositionSizingModeResponse {
  mode?: string; // 当前 mode，formula | fixed | kelly（kelly 仅记录，不允许通过 set 接口切到）
  maxPosition?: number; // 单股最大仓位比例（小数，0.3 = 30%）
  minPosition?: number; // 单股最小仓位比例（小数，0.03 = 3%）
  tiers?: Tier[]; // fixed 模式的评分分档表（按 minScore 倒序）
  description?: string; // 当前 mode 的简介，便于前端展示
}

// fixed 模式单档配置
interface Tier {
  minScore?: number; // 评分下限（score ≥ minScore 时落入此档）
  weight?: number; // 该档对应的单股仓位权重（小数，0.08 = 8%）
}

// 查询 position_sizing.mode 请求（入参留作扩展，当前忽略）
interface PositionSizingModeGetRequest {
  policyVersion?: string; // 策略版本（预留扩展，当前 Facade 忽略）
}

// CommonResultListWhatIfOptionResponse 响应数据
interface CommonResultListWhatIfOptionResponse {
  result?: number;
  message?: string;
  data?: WhatIfOptionResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 失效预案选项
interface WhatIfOptionResponse {
  code?: string; // 枚举码，存库时写入 whatIf 字段
  labelZh?: string; // 中文说明
  labelEn?: string; // 英文说明
}

// 交易计划状态变更请求
interface TradePlanStatusRequest {
  id: number; // 计划 ID
  status: string; // 目标状态：ACTIVE=激活, CLOSED=关闭
}

// 交易计划保存请求
interface TradePlanSaveRequest {
  id?: number; // 交易计划主键 ID；为空时新增
  stockCode: string; // 股票代码
  planName: string; // 计划名称
  planStatus?: string; // 计划状态，例如 DRAFT、READY、EXECUTING、DONE、CANCELLED
  signal?: string; // 计划来源信号，例如 BUY、HOLD、WATCH、SELL
  riskLevel?: string; // 风险等级，例如 LOW、MEDIUM、HIGH
  suggestedPositionRatio?: number; // 建议仓位比例
  executionCapital?: number; // 执行资金；为空时不计算计划金额
  stopLossPct?: number; // 止损比例
  takeProfitPct?: number; // 止盈比例
  targetPrice?: number; // 目标价格
  planNote?: string; // 计划备注
  why?: string; // Why：计划理由，可人工编辑或由规则生成
  whatAction?: string; // What：计划动作，可人工编辑或由策略变量覆盖
  whenCondition?: string; // When：执行条件或时机，可人工编辑或由策略变量覆盖
  whatIf?: string; // What If：失效预案，可人工编辑或由规则生成
}

// 通用分页查询请求
interface SimplePageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  keyword?: string; // 关键字，用于编码、名称等模糊查询
  status?: string; // 状态筛选，例如 enabled、disabled、DRAFT、SUCCESS
  type?: string; // 类型筛选，例如任务类型、股票池类型
  startDate?: string; // 开始日期，格式 yyyy-MM-dd
  endDate?: string; // 结束日期，格式 yyyy-MM-dd
  signals?: string[]; // 推荐信号多选过滤（与 type 字段同时存在时优先使用 signals）
  riskLevels?: string[]; // 风险等级多选过滤
  minScore?: number; // 推荐评分下限（含），用于推荐结果列表过滤
  maxScore?: number; // 推荐评分上限（含），用于推荐结果列表过滤
  industry?: string; // 行业过滤，按 stock_info.industry 精确匹配
  analysisDate?: string; // 分析日期，格式 yyyy-MM-dd；推荐结果查询时不传则取数据库最新一天
  policyVersion?: string; // 按 policy_version 过滤（如 p1.14-morning / p1.14-eod / p1.13-north-contaminated），null 不过滤
}

// PageResultTradePlanResponse 响应数据
interface PageResultTradePlanResponse {
  result?: number;
  message?: string;
  data?: TradePlanResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 交易计划列表行响应
interface TradePlanResponse {
  id?: number; // 交易计划 ID
  planCode?: string; // 交易计划业务编码
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称（关联 stock_info.stock_name）
  planName?: string; // 计划名称
  planStatus?: string; // 计划状态：DRAFT / ACTIVE / CLOSED
  signal?: string; // 来源信号
  riskLevel?: string; // 风险等级：LOW / MEDIUM / HIGH
  suggestedPositionRatio?: number; // 建议仓位比例
  executionCapital?: number; // 按整手对齐后的实际执行金额；suggestedLots=0 时为 0
  plannedAmount?: number; // 计划金额（执行金额 × 仓位比例）
  stopLossPct?: number; // 止损比例（小数）
  takeProfitPct?: number; // 止盈比例（小数）
  targetPrice?: number; // 目标价
  planNote?: string; // 计划备注（资金不足时含警告文案）
  suggestedLots?: number; // 建议买入手数（A 股 1 手 = 100 股），0 表示资金不足以买 1 手或行情缺失
  whatAction?: string; // 4W 动作代码
  whenCondition?: string; // 4W 触发条件代码
  why?: string; // 买入理由 JSON 字符串
  whatIf?: string; // 风控预案 JSON 字符串
  createdAt?: string; // 创建时间
}

// 从推荐结果生成交易计划草稿的请求
interface TradePlanFromRecommendRequest {
  recommendResultId: number; // 推荐结果主键 ID
  executionCapital?: number; // 执行资金；为空时不生成计划金额
  why?: string; // Why：计划理由覆盖值
  whatAction?: string; // What：计划动作覆盖值
  whenCondition?: string; // When：执行条件覆盖值
  whatIf?: string; // What If：失效预案覆盖值
}

// 系统配置保存请求
interface SystemConfigSaveRequest {
  configKey: string; // 配置键，业务唯一
  configValue: string; // 配置值，必须是合法 JSON 字符串
  description?: string; // 配置说明
}

// PageResultMapStringObject 接口
interface PageResultMapStringObject {
  result?: number;
  message?: string;
  data?: Record<string, Record<string, any>>[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 同步任务更新请求
interface SyncJobUpdateRequest {
  id: number; // 任务主键 ID
  jobType?: string; // 任务类型，例如 DAILY_PIPELINE、QUOTE_SYNC
  analysisScene?: string; // 分析场景或任务来源
}

// 任务触发请求
interface TaskTriggerRequest {
  jobType?: string; // 任务类型，例如 DAILY_PIPELINE、QUOTE_SYNC、ENRICHMENT_SYNC
  analysisScene?: string; // 分析场景或任务来源
  parameters?: Record<string, Record<string, any>>; // 任务扩展参数，保存到任务 metadata
}

// CommonResultSyncJobStatusResponse 响应数据
interface CommonResultSyncJobStatusResponse {
  result?: number;
  message?: string;
  data?: SyncJobStatusResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 同步任务状态快照响应
interface SyncJobStatusResponse {
  id?: number; // 任务主键 ID
  jobCode?: string; // 任务编码
  jobType?: string; // 任务类型
  analysisScene?: string; // 分析场景
  status?: string; // 任务状态：PENDING、RUNNING、SUCCESS、FAILED、CANCELLED
  cancelRequested?: boolean; // 是否已请求取消，Python worker 尚未处理时为 true
  startedTime?: string; // 任务创建或开始时间
  finishedTime?: string; // 任务完成时间，未完成时为 null
  processedCount?: number; // 处理总数
  successCount?: number; // 成功数量
  failedCount?: number; // 失败数量
  errorMessage?: string; // 失败原因，成功时为 null
}

// 股票池保存请求
interface StockPoolSaveRequest {
  id?: number; // 股票池主键 ID；为空时新增
  poolCode: string; // 股票池编码，业务唯一
  poolName: string; // 股票池名称
  poolType: string; // 股票池类型，例如 WATCHLIST、RECOMMEND、SECTOR
  enabled?: boolean; // 是否启用
  sortOrder?: number; // 排序值，数字越小越靠前
  description?: string; // 股票池说明
}

// 股票池明细保存请求
interface StockPoolItemSaveRequest {
  id?: number; // 明细主键 ID；为空时新增
  poolId: number; // 所属股票池 ID
  stockCode: string; // 股票代码
  stockName: string; // 股票名称
  industry?: string; // 所属行业
  focusLevel?: string; // 关注级别，例如 LOW、MEDIUM、HIGH
  enabled?: boolean; // 是否启用
  sortOrder?: number; // 池内排序
}

// CommonResultStockPoolItemSaveResponse 响应数据
interface CommonResultStockPoolItemSaveResponse {
  result?: number;
  message?: string;
  data?: StockPoolItemSaveResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 股票池明细保存结果
interface StockPoolItemSaveResponse {
  id?: number; // 明细主键 ID（新增时回填生成的 ID）
  action?: string; // 处理结果代码：SAVED_ONLY / ANALYZED / QUEUED
  triggeredJobCode?: string; // 派发的分析任务编码（仅 action=ANALYZED 时有值）
  existingQuoteDays?: number; // 数据库中已有的该股票行情天数
  message?: string; // 用户友好提示文案
}

// 股票池明细分页请求
interface StockPoolItemPageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  poolId?: number; // 股票池 ID；为空时查询所有池内股票
  keyword?: string; // 关键字，用于股票代码或股票名称模糊查询
  enabled?: boolean; // 启用状态筛选
  liquidityBucket?: string; // 流动性档位（LiquidityBucketEnum）
}

// CommonResultListSignalExplanationResponse 响应数据
interface CommonResultListSignalExplanationResponse {
  result?: number;
  message?: string;
  data?: SignalExplanationResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 信号说明选项
interface SignalExplanationResponse {
  explanation?: string; // 双语说明文本，如 MACD 在信号线上方（MACD above signal line）
  category?: string; // 分类：MACD / MA / RSI / KDJ / BOLL / VOLUME / FUND_FLOW / FUNDAMENTAL 等
  sortOrder?: number; // 展示排序，数值越小越靠前
}

// 报告分页查询请求
interface StockReportPageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  reportType?: string; // 报告类型：DAILY=日报, WEEKLY=周报；不传则查全部
}

// PageResultStockReportResponse 响应数据
interface PageResultStockReportResponse {
  result?: number;
  message?: string;
  data?: StockReportResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 日报 / 周报摘要
interface StockReportResponse {
  id?: number; // 主键 ID
  reportDate?: string; // 报告日期
  reportType?: string; // 报告类型：DAILY=日报, WEEKLY=周报
  title?: string; // 报告标题
  status?: string; // 报告状态：GENERATED=已生成
  content?: string; // 报告正文（JSON 字符串），包含信号分布、重点股票等
}

// CommonResultStockReportResponse 响应数据
interface CommonResultStockReportResponse {
  result?: number;
  message?: string;
  data?: StockReportResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 推荐规则保存请求
interface RecommendRuleSaveRequest {
  id?: number; // 推荐规则主键 ID；为空时新增
  ruleCode: string; // 推荐规则编码，业务唯一
  ruleName: string; // 推荐规则名称
  enabled?: boolean; // 是否启用该规则
  minScore?: number; // 最低推荐评分
  maxRiskLevel?: string; // 允许推荐的最高风险等级
  recommendLimit?: number; // 推荐数量上限
  cashReserveRatio?: number; // 现金保留比例
  maxSinglePositionRatio?: number; // 单股最高仓位比例
}

// PageResultRecommendResultResponse 响应数据
interface PageResultRecommendResultResponse {
  result?: number;
  message?: string;
  data?: RecommendResultResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 告警分页查询请求
interface AlertPageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
  stockCode?: string; // 股票代码；为空时查询全部股票告警
  alertType?: string; // 告警类型，例如 RISK、SELL_SIGNAL、DATA_QUALITY
  severity?: string; // 告警严重级别，例如 LOW、MEDIUM、HIGH
  handled?: boolean; // 处理状态；为空时不过滤处理状态
  alertTypes?: string[]; // 告警类型多选过滤（可与 alertType 二选一；同时存在时优先使用 alertTypes）
}

// 告警处理请求
interface AlertHandleRequest {
  id: number; // 告警事件主键 ID
  handled?: boolean; // 是否已处理，默认标记为已处理
}

// 回测交易明细分页查询请求
interface BacktestTradePageRequest {
  runId: number; // 回测主键 ID（BIGINT）
  side?: string; // 交易方向过滤：BUY / SELL；为空不过滤
  exitReason?: string; // 退出原因过滤：STOP_LOSS / TAKE_PROFIT / HOLD_PERIOD / SIGNAL；为空不过滤
  page?: number; // 页码，从 1 开始
  pageSize?: number; // 每页条数
}

// 回测单笔交易响应
interface BacktestTradeResponse {
  id?: number; // 交易主键 ID
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称，stock_info 未维护时为空
  tradeDate?: string; // 成交日（yyyy-MM-dd）
  side?: string; // BUY / SELL
  shares?: number; // 成交股数
  price?: number; // 成交价（含撮合口径）
  commission?: number; // 佣金 + 印花税 + 过户费合计
  pnl?: number; // 盈亏（金额），仅 SELL 行有值
  triggeredRules?: string[]; // 触发规则码数组（BUY 行有值，SELL 行通常为空）
  exitReason?: string; // 退出原因：STOP_LOSS / TAKE_PROFIT / HOLD_PERIOD / SIGNAL
  holdingDays?: number; // 持仓天数（SELL 行有值）
}

// PageResultBacktestTradeResponse 响应数据
interface PageResultBacktestTradeResponse {
  result?: number;
  message?: string;
  data?: BacktestTradeResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 回测持仓明细分页查询请求
interface BacktestPositionRequest {
  runId: string; // 回测任务业务唯一标识
  page?: number; // 页码，从 1 开始
  pageSize?: number; // 每页条数
}

// 回测持仓明细响应
interface BacktestPositionResponse {
  id?: number; // 主键 ID
  runId?: string; // 关联回测任务 run_id
  stockCode?: string; // 股票代码
  stockName?: string; // 股票名称，stock_info 未维护时为空
  entryDate?: string; // 建仓日期
  exitDate?: string; // 平仓日期；持仓中为 null
  entryPrice?: number; // 建仓价格
  exitPrice?: number; // 平仓价格；持仓中为 null
  positionRatio?: number; // 仓位比例（0-1）
  pnl?: number; // 绝对盈亏（元）
  pnlRatio?: number; // 收益率（小数，0.05 = 5%）
  exitReason?: string; // 平仓原因：STOP_LOSS / TAKE_PROFIT / HOLD_PERIOD / SIGNAL_REVERSE
  entryMetadata?: string; // 入场归因 JSON，含推荐排序、有效分、仓位解释和过滤配置
}

// PageResultBacktestPositionResponse 响应数据
interface PageResultBacktestPositionResponse {
  result?: number;
  message?: string;
  data?: BacktestPositionResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 回测任务列表查询请求
interface BacktestListRequest {
  page?: number; // 页码，从 1 开始
  pageSize?: number; // 每页条数
  status?: string; // 状态过滤：PENDING / RUNNING / SUCCESS / FAILED / CANCELLED；为空不过滤
}

// 回测任务摘要响应
interface BacktestRunResponse {
  id?: number; // 主键 ID
  runId?: string; // 业务唯一标识
  startDate?: string; // 回测起始日期
  endDate?: string; // 回测结束日期
  ruleVersion?: string; // 规则版本
  initialCapital?: number; // 初始资金（元）
  totalReturn?: number; // 总收益率（小数，0.15 = 15%）
  annualizedReturn?: number; // 年化收益率（小数）
  sharpeRatio?: number; // 夏普比率
  maxDrawdown?: number; // 最大回撤（小数，负值）
  winRate?: number; // 胜率（小数，0.6 = 60%）
  tradeCount?: number; // 总交易次数
  status?: string; // 状态：PENDING / RUNNING / SUCCESS / FAILED / CANCELLED
  errorMessage?: string; // 失败时的错误信息
  createdAt?: string; // 任务创建时间（yyyy-MM-dd HH:mm）
  finishedAt?: string; // 任务完成时间（yyyy-MM-dd HH:mm）
  configJson?: string; // 回测配置 JSON（详情接口返回，列表接口为 null）
  diagnosticJson?: string; // 回测诊断 JSON（详情接口返回，含输入覆盖率和低覆盖警告）
}

// PageResultBacktestRunResponse 响应数据
interface PageResultBacktestRunResponse {
  result?: number;
  message?: string;
  data?: BacktestRunResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 按 run_id 操作回测任务请求
interface BacktestRunIdRequest {
  runId: string; // 回测任务业务唯一标识
}

// CommonResultBacktestRunResponse 响应数据
interface CommonResultBacktestRunResponse {
  result?: number;
  message?: string;
  data?: BacktestRunResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 回测任务创建请求
interface BacktestCreateRequest {
  startDate: string; // 回测起始日期（格式 YYYY-MM-DD）
  endDate: string; // 回测结束日期（格式 YYYY-MM-DD）
  ruleVersion?: string; // 规则版本，如 p1.8；为空时使用最新版本
  initialCapital: number; // 初始资金（元）
  remark?: string; // 备注说明
}

// 回测任务分页查询请求
interface BacktestRunPageRequest {
  name?: string; // 回测任务名称模糊匹配（LIKE），为空不过滤
  status?: string; // 状态过滤：PENDING / RUNNING / SUCCESS / FAILED / CANCELLED；为空不过滤
  startDateFrom?: string; // 回测起始日期下限（含），yyyy-MM-dd；为空不过滤
  startDateTo?: string; // 回测起始日期上限（含），yyyy-MM-dd；为空不过滤
  positionSizingMode?: string; // 按仓位算法 mode 过滤：formula / fixed；null 表示不过滤
  page?: number; // 页码，从 1 开始
  pageSize?: number; // 每页条数
}

// 回测任务列表项响应
interface BacktestRunSummaryResponse {
  id?: number; // 主键 ID（BIGINT）
  runId?: string; // 业务唯一标识 run_id
  name?: string; // 回测任务名称
  status?: string; // 状态：PENDING / RUNNING / SUCCESS / FAILED / CANCELLED
  startDate?: string; // 回测起始日期（yyyy-MM-dd）
  endDate?: string; // 回测结束日期（yyyy-MM-dd）
  totalReturn?: number; // 总收益率（小数）
  sharpeRatio?: number; // 夏普比率
  maxDrawdown?: number; // 最大回撤（小数，约定负值或绝对值见报告）
  winRate?: number; // 胜率（小数）
  tradeCount?: number; // 交易次数
  createdAt?: string; // 任务创建时间（yyyy-MM-dd HH:mm）
  finishedAt?: string; // 任务完成时间（yyyy-MM-dd HH:mm），未完成为空
  positionSizingMode?: string; // 回测使用的仓位算法 mode：formula / fixed
}

// PageResultBacktestRunSummaryResponse 响应数据
interface PageResultBacktestRunSummaryResponse {
  result?: number;
  message?: string;
  data?: BacktestRunSummaryResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 按主键 id 查询回测子表请求
interface BacktestIdRequest {
  id: number; // 回测主键 ID（BIGINT）
}

// 回测净值曲线单点响应
interface BacktestEquityPointResponse {
  tradeDate?: string; // 交易日（yyyy-MM-dd）
  netValue?: number; // 净值（金额）
  cash?: number; // 现金
  positionValue?: number; // 持仓市值
  dailyReturn?: number; // 当日收益率（小数）
  drawdown?: number; // 当日回撤（相对历史峰值，小数）
}

// CommonResultListBacktestEquityPointResponse 响应数据
interface CommonResultListBacktestEquityPointResponse {
  result?: number;
  message?: string;
  data?: BacktestEquityPointResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 回测详情查询请求
interface BacktestDetailRequest {
  id: number; // 回测主键 ID（BIGINT）
}

// 回测详情响应
interface BacktestDetailResponse {
  id?: number; // 主键 ID（BIGINT）
  runId?: string; // 业务唯一标识 run_id
  name?: string; // 回测任务名称
  configJson?: string; // 回测配置 JSON 原文，由前端解析
  diagnosticJson?: string; // 回测诊断 JSON 原文（输入覆盖率 / 低覆盖警告等）
  startDate?: string; // 回测起始日期（yyyy-MM-dd）
  endDate?: string; // 回测结束日期（yyyy-MM-dd）
  ruleVersion?: string; // 规则版本
  pipelineVersion?: string; // Python pipeline 版本快照
  initialCapital?: number; // 初始资金（元）
  totalReturn?: number; // 总收益率（小数）
  annualizedReturn?: number; // 年化收益率（小数）
  sharpeRatio?: number; // 夏普比率
  calmarRatio?: number; // Calmar 比率（年化收益 / 最大回撤绝对值）
  maxDrawdown?: number; // 最大回撤（小数）
  maxDrawdownDays?: number; // 最大回撤持续天数
  winRate?: number; // 胜率（小数）
  profitLossRatio?: number; // 盈亏比
  monthlyWinRate?: number; // 月度胜率（盈利月份占比）
  tradeCount?: number; // 总交易次数（来自 stock_backtest_run.trade_count）
  totalTrades?: number; // 总交易次数（来自 stock_backtest_result.total_trades，与 tradeCount 可能差异）
  metricsJson?: string; // 完整指标 JSON 原文，供 HTML / 前端报告渲染
  status?: string; // 状态：PENDING / RUNNING / SUCCESS / FAILED / CANCELLED
  errorMessage?: string; // 失败时的错误信息
  createdBy?: string; // 创建人
  createdAt?: string; // 任务创建时间（yyyy-MM-dd HH:mm）
  startedAt?: string; // 任务开始时间（yyyy-MM-dd HH:mm）
  finishedAt?: string; // 任务完成时间（yyyy-MM-dd HH:mm）
  positionSizingMode?: string; // 回测使用的仓位算法 mode：formula / fixed
}

// CommonResultBacktestDetailResponse 响应数据
interface CommonResultBacktestDetailResponse {
  result?: number;
  message?: string;
  data?: BacktestDetailResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 单个搜索批次的 trial 列表查询请求
interface BayesianTrialsRequest {
  searchId: string; // 搜索批次唯一标识
}

// 贝叶斯参数搜索单个 trial
interface BayesianTrialResponse {
  id?: number; // 记录 ID
  searchId?: string; // 搜索批次唯一标识
  trialId?: number; // trial 序号(同批次内递增)
  params?: string; // 参数组合 JSON 字符串,如 {"stop_loss_atr":"2.50","take_profit_pct":"0.15"}
  objectiveValue?: number; // 目标指标值
  sharpe?: number; // Sharpe 比率
  calmar?: number; // Calmar 比率
  maxDrawdown?: number; // 最大回撤(%, 负值)
  totalReturn?: number; // 总收益率(%, 小数)
  winRate?: number; // 胜率(%, 小数)
  runtimeSeconds?: number; // 单次 trial 跑批秒数
  objectiveMetric?: string; // 目标指标名:sharpe / calmar / total_return
  createdAt?: string; // 创建时间
}

// CommonResultListBayesianTrialResponse 响应数据
interface CommonResultListBayesianTrialResponse {
  result?: number;
  message?: string;
  data?: BayesianTrialResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 贝叶斯参数搜索批次列表查询请求
interface BayesianSearchListRequest {
  limit?: number; // 返回 top N 个最近批次,默认 20
}

// 贝叶斯参数搜索批次摘要
interface BayesianSearchSummaryResponse {
  searchId?: string; // 搜索批次唯一标识(bs-<uuid8>)
  objectiveMetric?: string; // 目标指标名:sharpe / calmar / total_return
  trialCount?: number; // trial 总数
  bestObjectiveValue?: number; // 最佳 trial 目标值
  bestTrialId?: number; // 最佳 trial 的 trial_id
  startedAt?: string; // 搜索启动时间(首个 trial created_at)
  finishedAt?: string; // 搜索结束时间(最后 trial created_at)
}

// CommonResultListBayesianSearchSummaryResponse 响应数据
interface CommonResultListBayesianSearchSummaryResponse {
  result?: number;
  message?: string;
  data?: BayesianSearchSummaryResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 回测单规则贡献响应
interface BacktestAttributionResponse {
  ruleCode?: string; // 规则码
  triggerCount?: number; // 触发次数
  winCount?: number; // 盈利交易数
  totalPnl?: number; // 累计盈亏（金额）
  avgPnlPerTrade?: number; // 单笔平均盈亏（金额）
  icMean?: number; // IC 均值
  icIr?: number; // IC 信息比率（mean / std）
}

// CommonResultListBacktestAttributionResponse 响应数据
interface CommonResultListBacktestAttributionResponse {
  result?: number;
  message?: string;
  data?: BacktestAttributionResponse[];
  errors?: ApiError[];
  ok?: boolean;
}

// 修改个人信息请求
interface UpdateProfileRequest {
  displayName?: string; // 显示昵称
  email?: string; // 邮箱
  avatar?: string; // 头像 URL
}

// CommonResultUserDetailResponse 响应数据
interface CommonResultUserDetailResponse {
  result?: number;
  message?: string;
  data?: UserDetailResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 登录用户详情
interface UserDetailResponse {
  userId?: number; // 用户 ID
  username?: string; // 登录用户名
  displayName?: string; // 显示昵称
  email?: string; // 邮箱
  avatar?: string; // 头像 URL
  roles?: string[]; // 角色 code 列表
  permissions?: string[]; // 权限码集合
  mustChangePassword?: boolean; // 是否需要强制修改密码
  lastLoginAt?: string; // 最近一次登录时间
  accountTotalCapital?: number; // 账户总资金（元），4W 仓位规划基数
}

// 更新当前用户账户总资金请求
interface UpdateCapitalRequest {
  accountTotalCapital: number; // 账户总资金（元），4W 仓位规划基数
}

// CommonResultBoolean 接口
interface CommonResultBoolean {
  result?: number;
  message?: string;
  data?: boolean;
  errors?: ApiError[];
  ok?: boolean;
}

// 登录请求
interface LoginRequest {
  username: string; // 用户名
  password: string; // 密码
}

// CommonResultLoginResponse 响应数据
interface CommonResultLoginResponse {
  result?: number;
  message?: string;
  data?: LoginResponse;
  errors?: ApiError[];
  ok?: boolean;
}

// 登录响应
interface LoginResponse {
  token?: string; // 访问令牌
  tokenType?: string; // token 类型
  expiresInSeconds?: number; // 过期秒数
  username?: string; // 登录用户名
  userId?: number; // 用户 ID
  displayName?: string; // 显示昵称
  roles?: string[]; // 角色 code 列表
  permissions?: string[]; // 权限码集合
}

// 修改密码请求
interface ChangePasswordRequest {
  oldPassword: string; // 当前密码（明文）
  newPassword: string; // 新密码（明文）
}

// 账户总资金变更历史分页查询请求
interface CapitalChangeLogPageRequest {
  pageNumber?: number; // 当前页码，从 1 开始
  pageSize?: number; // 每页记录数
  sortField?: string; // 排序字段，预留给列表接口使用
  sortOrder?: string; // 排序方向，取值 ASC 或 DESC
}

// 账户总资金变更历史记录
interface CapitalChangeLogResponse {
  id?: number; // 日志主键
  oldCapital?: number; // 调整前金额（元），首次设置为 null
  newCapital?: number; // 调整后金额（元）
  changeAmount?: number; // 差额（new - old），首次为 null
  operatorName?: string; // 操作人名称
  changeTime?: string; // 变更时间，ISO 字符串
  reason?: string; // 变更备注，nullable
}

// PageResultCapitalChangeLogResponse 响应数据
interface PageResultCapitalChangeLogResponse {
  result?: number;
  message?: string;
  data?: CapitalChangeLogResponse[];
  errors?: ApiError[];
  currentPage?: number;
  pageSize?: number;
  totalPage?: number;
  total?: number;
  ok?: boolean;
}

// 枚举列表查询请求
interface EnumListRequest {
  enumType?: string; // 枚举类型；为空时返回全部支持的枚举
}

// CommonResultMapStringListEnumItemDTO 数据传输对象
interface CommonResultMapStringListEnumItemDTO {
  result?: number;
  message?: string;
  data?: Record<string, EnumItemDTO[]>;
  errors?: ApiError[];
  ok?: boolean;
}

// 枚举项
interface EnumItemDTO {
  enumType?: string; // 枚举类型
  enumValue?: string; // 枚举值
  localLanguage?: string; // 中文展示文案
  enumOrder?: number; // 枚举展示排序
  displayName?: string; // 前端展示名称
  description?: string; // 业务说明，前端 tooltip 展示（规则触发条件、信号强弱、专业解读等）
};
    }