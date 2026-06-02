import Drawer from '@/components/Drawer';
import ResizableTable from '@/components/ResizableTable';
import { postIndicators } from '@/services/apis/stockapi';
import type { ProColumns } from '@ant-design/pro-components';
import { useCallback, useEffect, useMemo, useState } from 'react';

type IndicatorRow = API.StockIndicatorDailyResponse;

export interface StockIndicatorDrawerContext {
  open: boolean;
  stockCode?: string;
  stockName?: string;
}

interface StockIndicatorDrawerProps {
  context: StockIndicatorDrawerContext;
  onCancel: () => void;
}

const TREND_COLS: ProColumns<IndicatorRow>[] = [
  { title: '日期', dataIndex: 'tradeDate', width: 110, fixed: 'left' },
  {
    title: 'MA5',
    dataIndex: 'ma5',
    valueType: 'digit',
    width: 85,
    tooltip:
      '5日简单移动平均线，反映近5个交易日的平均成本。价格站上MA5视为短期偏多，跌破MA5视为短期偏空',
  },
  {
    title: 'MA10',
    dataIndex: 'ma10',
    valueType: 'digit',
    width: 85,
    tooltip:
      '10日简单移动平均线，反映近10个交易日的平均成本。常与MA5配合，MA5上穿MA10为短期金叉买入信号',
  },
  {
    title: 'MA20',
    dataIndex: 'ma20',
    valueType: 'digit',
    width: 85,
    tooltip:
      '20日简单移动平均线，同时也是布林带中轨的基准。是判断中期趋势的重要参考，也称"月线均线"',
  },
  {
    title: 'MACD(DIF)',
    dataIndex: 'macd',
    valueType: 'digit',
    width: 100,
    tooltip:
      'MACD快慢线差值：DIF = EMA(12) - EMA(26)。正值表示短期均线在长期均线上方（多头），负值反之（空头）',
  },
  {
    title: 'Signal(DEA)',
    dataIndex: 'macdSignal',
    valueType: 'digit',
    width: 110,
    tooltip:
      'MACD信号线：DEA = DIF的9日指数移动均线。DIF上穿DEA形成"金叉"（买入参考）；DIF下穿DEA形成"死叉"（卖出参考）',
  },
  {
    title: 'MACD柱',
    dataIndex: 'macdHistogram',
    valueType: 'digit',
    width: 95,
    tooltip:
      'MACD柱状图：MACD柱 = DIF - DEA。红柱（正值）表示多头动能增强；绿柱（负值）表示空头动能增强；柱子由短变长说明趋势加速',
  },
  {
    title: 'RSI(14)',
    dataIndex: 'rsi',
    valueType: 'digit',
    width: 90,
    tooltip:
      '14日相对强弱指数，范围0～100。通常 >70 为超买区（可能回调），<30 为超卖区（可能反弹），50 为多空分界线',
  },
];

const OSCILLATOR_COLS: ProColumns<IndicatorRow>[] = [
  {
    title: 'KDJ-K',
    dataIndex: 'kdjK',
    valueType: 'digit',
    width: 85,
    tooltip:
      'KDJ指标的K值，范围0～100。K值对价格变化反应最灵敏，K上穿D形成金叉（短期买入参考），>80 表示超买',
  },
  {
    title: 'KDJ-D',
    dataIndex: 'kdjD',
    valueType: 'digit',
    width: 85,
    tooltip: 'KDJ指标的D值，是K值的3日均线，范围0～100。D值较K值平滑，>80 为超买区，<20 为超卖区',
  },
  {
    title: 'KDJ-J',
    dataIndex: 'kdjJ',
    valueType: 'digit',
    width: 85,
    tooltip:
      'KDJ指标的J值：J = 3K - 2D，范围不限于0～100。J值最为灵敏，>100 为强超买，<0 为强超卖，常作为短线买卖点参考',
  },
  {
    title: '布林上轨',
    dataIndex: 'bollUpper',
    valueType: 'digit',
    width: 100,
    tooltip:
      '布林带上轨 = 中轨 + 2倍标准差。价格触及上轨时可能遇到阻力回调；有效突破并站稳上轨则表示强势上涨行情',
  },
  {
    title: '布林中轨',
    dataIndex: 'bollMiddle',
    valueType: 'digit',
    width: 100,
    tooltip:
      '布林带中轨 = 20日移动平均线。是价格的回归中枢，也是多空力量的分界线，价格上方偏多、下方偏空',
  },
  {
    title: '布林下轨',
    dataIndex: 'bollLower',
    valueType: 'digit',
    width: 100,
    tooltip:
      '布林带下轨 = 中轨 - 2倍标准差。价格触及下轨时可能获得支撑反弹；有效跌破下轨则表示弱势下跌行情',
  },
  {
    title: 'ATR(14)',
    dataIndex: 'atr14',
    valueType: 'digit',
    width: 90,
    tooltip:
      '14日平均真实波幅（Average True Range）。衡量市场波动程度，数值越大表示日内价格波动越剧烈，可用于设置止损幅度参考（如止损 = 1.5倍ATR）',
  },
  {
    title: '量比',
    dataIndex: 'volumeRatio',
    valueType: 'digit',
    width: 85,
    tooltip:
      '量比 = 当日成交量 ÷ 近5日平均成交量。>1 表示今日成交活跃，>2 放量，>5 为显著放量；<0.5 为明显缩量。放量上涨看多，缩量下跌看空',
  },
  {
    title: '波动率(20)',
    dataIndex: 'volatility20d',
    valueType: 'digit',
    width: 100,
    tooltip:
      '近20个交易日收益率的标准差（日波动率）。数值越大说明近期价格波动越剧烈、风险越高；数值越小说明走势越平稳',
  },
];

const buildColumns = (): ProColumns<IndicatorRow>[] => {
  return [...TREND_COLS, ...OSCILLATOR_COLS];
};

const StockIndicatorDrawer: React.FC<StockIndicatorDrawerProps> = ({ context, onCancel }) => {
  const { open, stockCode, stockName } = context;
  const [data, setData] = useState<IndicatorRow[]>([]);
  const [loading, setLoading] = useState(false);

  const loadIndicators = useCallback(async () => {
    if (!stockCode) return;
    setLoading(true);
    try {
      const res = await postIndicators({ stockCode });
      setData(res.data ?? []);
    } catch {
      // 错误已由 fetch 封装处理
    } finally {
      setLoading(false);
    }
  }, [stockCode]);

  const columns = useMemo(buildColumns, []);

  useEffect(() => {
    if (open && stockCode) {
      loadIndicators();
    } else {
      setData([]);
    }
  }, [open, stockCode, loadIndicators]);

  return (
    <Drawer
      title={`技术指标 — ${stockName ?? stockCode ?? ''}`}
      open={open}
      onCancel={onCancel}
      size="large"
      destroyOnHidden
      showOkButton={false}
    >
      <ResizableTable<IndicatorRow>
        rowKey="tradeDate"
        dataSource={data}
        loading={loading}
        columns={columns}
        search={false}
        pagination={{ pageSize: 30, hideOnSinglePage: true }}
        scroll={{ x: 1700 }}
        toolBarRender={false}
        options={false}
      />
    </Drawer>
  );
};

export default StockIndicatorDrawer;
