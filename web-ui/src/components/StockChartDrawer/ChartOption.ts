import type { EChartsOption } from 'echarts';

type QuoteRow = API.StockQuoteDailyResponse;
type IndicatorRow = API.StockIndicatorDailyResponse;

interface SeriesData {
  dates: string[];
  ohlc: (number | null)[][];
  volume: { value: number; itemStyle: { color: string } }[];
  ma5: (number | null)[];
  ma10: (number | null)[];
  ma20: (number | null)[];
  bollUpper: (number | null)[];
  bollMiddle: (number | null)[];
  bollLower: (number | null)[];
  macdHist: ({ value: number; itemStyle: { color: string } } | null)[];
  macdLine: (number | null)[];
  deaLine: (number | null)[];
  rsiLine: (number | null)[];
}

// 把行情/指标按日期对齐，转成 ECharts 序列所需的扁平数组
function buildSeriesData(quotes: QuoteRow[], indicators: IndicatorRow[]): SeriesData {
  const indMap = new Map(indicators.map((i) => [i.tradeDate ?? '', i]));
  const quoteMap = new Map(quotes.map((q) => [q.tradeDate ?? '', q]));
  const dates = indicators.map((i) => i.tradeDate ?? '');

  const ohlc = dates.map((d) => {
    const q = quoteMap.get(d);
    return q
      ? [q.openPrice ?? null, q.closePrice ?? null, q.lowPrice ?? null, q.highPrice ?? null]
      : [null, null, null, null];
  });

  const volume = dates.map((d) => {
    const q = quoteMap.get(d);
    if (!q) {
      return { value: 0, itemStyle: { color: '#8c8c8c' } };
    }
    const isUp = (q.closePrice ?? 0) >= (q.openPrice ?? 0);
    return { value: q.volume ?? 0, itemStyle: { color: isUp ? '#ef5350' : '#26a69a' } };
  });

  const macdHist = dates.map((d) => {
    const v = indMap.get(d)?.macdHistogram ?? null;
    if (v === null) return null;
    return { value: v, itemStyle: { color: v >= 0 ? '#ef5350' : '#26a69a' } };
  });

  return {
    dates,
    ohlc,
    volume,
    ma5: dates.map((d) => indMap.get(d)?.ma5 ?? null),
    ma10: dates.map((d) => indMap.get(d)?.ma10 ?? null),
    ma20: dates.map((d) => indMap.get(d)?.ma20 ?? null),
    bollUpper: dates.map((d) => indMap.get(d)?.bollUpper ?? null),
    bollMiddle: dates.map((d) => indMap.get(d)?.bollMiddle ?? null),
    bollLower: dates.map((d) => indMap.get(d)?.bollLower ?? null),
    macdHist,
    macdLine: dates.map((d) => indMap.get(d)?.macd ?? null),
    deaLine: dates.map((d) => indMap.get(d)?.macdSignal ?? null),
    rsiLine: dates.map((d) => indMap.get(d)?.rsi ?? null),
  };
}

// K 线 + 均线 + 布林带（共享主图区）
function buildPriceSeries(s: SeriesData): EChartsOption['series'] {
  const lineCfg = (data: (number | null)[], color: string, dashed = false) => ({
    type: 'line' as const,
    data,
    xAxisIndex: 0,
    yAxisIndex: 0,
    smooth: true,
    symbol: 'none' as const,
    lineStyle: { color, width: 1, ...(dashed ? { type: 'dashed' as const } : {}) },
  });
  return [
    {
      name: 'K线',
      type: 'candlestick' as const,
      data: s.ohlc,
      xAxisIndex: 0,
      yAxisIndex: 0,
      // A 股惯例：红涨绿跌
      itemStyle: {
        color: '#ef5350',
        color0: '#26a69a',
        borderColor: '#ef5350',
        borderColor0: '#26a69a',
      },
    },
    { name: 'MA5', ...lineCfg(s.ma5, '#FF9800') },
    { name: 'MA10', ...lineCfg(s.ma10, '#2196F3') },
    { name: 'MA20', ...lineCfg(s.ma20, '#9C27B0') },
    { name: 'BOLL上', ...lineCfg(s.bollUpper, '#90CAF9', true) },
    { name: 'BOLL中', ...lineCfg(s.bollMiddle, '#CE93D8', true) },
    { name: 'BOLL下', ...lineCfg(s.bollLower, '#90CAF9', true) },
  ];
}

// 成交量 + MACD + RSI 三个副图
function buildSubSeries(s: SeriesData): EChartsOption['series'] {
  return [
    { name: '成交量', type: 'bar' as const, data: s.volume, xAxisIndex: 1, yAxisIndex: 1 },
    { name: 'MACD柱', type: 'bar' as const, data: s.macdHist, xAxisIndex: 2, yAxisIndex: 2 },
    {
      name: 'DIF',
      type: 'line' as const,
      data: s.macdLine,
      xAxisIndex: 2,
      yAxisIndex: 2,
      smooth: true,
      symbol: 'none' as const,
      lineStyle: { color: '#FF9800', width: 1 },
    },
    {
      name: 'DEA',
      type: 'line' as const,
      data: s.deaLine,
      xAxisIndex: 2,
      yAxisIndex: 2,
      smooth: true,
      symbol: 'none' as const,
      lineStyle: { color: '#2196F3', width: 1 },
    },
    {
      name: 'RSI14',
      type: 'line' as const,
      data: s.rsiLine,
      xAxisIndex: 3,
      yAxisIndex: 3,
      smooth: true,
      symbol: 'none' as const,
      lineStyle: { color: '#FF9800', width: 1.5 },
      // 30/70 参考线：超卖区 / 超买区分界
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#888', type: 'dashed' as const, width: 1 },
        data: [{ yAxis: 30 }, { yAxis: 70 }],
      },
    },
  ];
}

// 4 个面板的 grid / xAxis / yAxis 定义
function buildLayout(dates: string[]): Pick<EChartsOption, 'grid' | 'xAxis' | 'yAxis'> {
  return {
    grid: [
      { left: 64, right: 16, top: 32, height: '40%' },
      { left: 64, right: 16, top: '56%', height: '10%' },
      { left: 64, right: 16, top: '70%', height: '12%' },
      { left: 64, right: 16, top: '86%', height: '8%' },
    ],
    xAxis: [0, 1, 2, 3].map((idx) => ({
      type: 'category' as const,
      data: dates,
      gridIndex: idx,
      // 只在最下面的 RSI 面板显示日期标签，其他面板共享同一 X 轴不显示
      axisLabel: { show: idx === 3, fontSize: 10 },
      axisTick: { show: false },
      axisLine: { show: idx === 3 },
      splitLine: { show: false },
    })),
    yAxis: [
      { scale: true, gridIndex: 0, splitNumber: 4, axisLabel: { fontSize: 10 } },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: {
          fontSize: 9,
          formatter: (v: number) => {
            if (v >= 1e8) return `${(v / 1e8).toFixed(0)}亿`;
            return `${(v / 1e4).toFixed(0)}万`;
          },
        },
      },
      { scale: true, gridIndex: 2, splitNumber: 2, axisLabel: { fontSize: 10 } },
      { min: 0, max: 100, gridIndex: 3, splitNumber: 2, axisLabel: { fontSize: 10 } },
    ],
  };
}

export function buildOption(quotes: QuoteRow[], indicators: IndicatorRow[]): EChartsOption {
  const data = buildSeriesData(quotes, indicators);
  const layout = buildLayout(data.dates);

  return {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: {
      data: ['K线', 'MA5', 'MA10', 'MA20', 'BOLL上', 'BOLL中', 'BOLL下'],
      top: 0,
      itemHeight: 8,
      textStyle: { fontSize: 11 },
    },
    // 所有面板共享同一 X 轴游标联动
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2, 3], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1, 2, 3], bottom: 4, height: 18 },
    ],
    ...layout,
    series: [...(buildPriceSeries(data) ?? []), ...(buildSubSeries(data) ?? [])],
  };
}
