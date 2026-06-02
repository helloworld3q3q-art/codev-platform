/**
 * 信号解释文本双语化工具。
 *
 * 历史数据中 explanation 字段为纯英文，新数据由 Python 写入后已是中英双语。
 * 此函数对纯英文文本做前缀匹配翻译，已含中文的文本原样透传。
 */

// [英文前缀, 中文含义] 顺序决定匹配优先级，较长前缀需放在较短同根前缀之前
const EXPLANATION_PREFIXES: [string, string][] = [
  // VOLATILITY_20D
  ['20-day volatility is high', '20日波动率偏高，注意风险'],
  ['20-day volatility is low', '20日波动率偏低，走势平稳'],
  ['20-day volatility is normal', '20日波动率正常'],
  ['Insufficient volatility data', '波动率数据不足'],
  // MACD_CROSS
  ['MACD above signal line', 'MACD 在信号线上方'],
  ['MACD below signal line', 'MACD 在信号线下方'],
  // RSI_LEVEL
  ['RSI oversold', 'RSI 超卖区，均值回归买入信号'],
  ['RSI overbought', 'RSI 超买区，注意回调风险'],
  ['RSI neutral', 'RSI 中性区间'],
  // MA_TREND
  ['Short-term moving averages are bullish', '短期均线多头排列'],
  ['Short-term moving averages are bearish', '短期均线空头排列'],
  ['Moving-average trend is mixed', '均线趋势混乱，多空信号交织'],
  ['Insufficient moving-average data', '均线数据不足'],
  // BOLL_POSITION
  ['Price is near the lower Bollinger band', '价格触及布林下轨，超卖反弹信号'],
  ['Price is near the upper Bollinger band', '价格触及布林上轨，注意回调压力'],
  ['Price is inside the Bollinger channel', '价格在布林通道内运行'],
  ['Insufficient Bollinger data', '布林带数据不足'],
  // VALUATION_LEVEL
  ['PE ratio is very high', 'PE估值偏高，注意泡沫风险'],
  ['Valuation is relatively low', '估值相对较低，具备安全边际'],
  ['PB ratio is high', 'PB市净率偏高'],
  ['Valuation filter is neutral', '估值处于中性区间'],
  // MAIN_FUND_FLOW
  ['Main fund flow is positive', '主力资金净流入，机构在买入'],
  ['Main fund flow is negative', '主力资金净流出，机构在减仓'],
  ['Fund flow is neutral', '主力资金流中性'],
  // DATA_QUALITY
  ['Data quality score', '数据质量评分'],
  // A_SHARE_LIMIT_ST
  ['A-share limit and ST risk rule', 'A股涨跌停 & ST 风险规则'],
];

const HAS_CHINESE = /[一-龥]/;

/**
 * 将信号解释文本转为中英双语格式。
 *
 * - 已含中文 → 原样返回（新数据已由 Python 写成双语）
 * - 纯英文 → 前缀匹配后输出「中文（原始英文）」
 * - 无匹配 → 原样返回（避免信息丢失）
 */
export const translateExplanation = (explanation: string | null | undefined): string => {
  if (!explanation) {
    return '-';
  }
  if (HAS_CHINESE.test(explanation)) {
    return explanation;
  }
  for (const [eng, chn] of EXPLANATION_PREFIXES) {
    if (explanation.startsWith(eng)) {
      return `${chn}（${explanation}）`;
    }
  }
  return explanation;
};
