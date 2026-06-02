import Drawer from '@/components/Drawer';
import { postIndicators, postQuotes } from '@/services/apis/stockapi';
import { FullscreenExitOutlined, FullscreenOutlined } from '@ant-design/icons';
import { Button, Empty, Spin } from 'antd';
import ReactECharts from 'echarts-for-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { buildOption } from './ChartOption';

type QuoteRow = API.StockQuoteDailyResponse;
type IndicatorRow = API.StockIndicatorDailyResponse;

export interface StockChartDrawerContext {
  open: boolean;
  stockCode?: string;
  stockName?: string;
}

interface StockChartDrawerProps {
  context: StockChartDrawerContext;
  onCancel: () => void;
}

const StockChartDrawer: React.FC<StockChartDrawerProps> = ({ context, onCancel }) => {
  const { open, stockCode, stockName } = context;
  const [quotes, setQuotes] = useState<QuoteRow[]>([]);
  const [indicators, setIndicators] = useState<IndicatorRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [drawerSize, setDrawerSize] = useState(1400);

  const handleToggleFullscreen = useCallback(() => {
    setFullscreen((prev) => {
      const next = !prev;
      setDrawerSize(next ? window.innerWidth : 1400);
      return next;
    });
  }, []);

  const titleNode = useMemo(
    () => (
      <div className="flex items-center">
        <span className="flex-1">{`K线图表 — ${stockName ?? stockCode ?? ''}`}</span>
        <Button
          type="text"
          size="small"
          icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
          onClick={handleToggleFullscreen}
        />
      </div>
    ),
    [stockName, stockCode, fullscreen, handleToggleFullscreen],
  );

  const loadData = useCallback(async () => {
    if (!stockCode) return;
    setLoading(true);
    try {
      // 显式拉 365 个自然日：MA20 / BOLL20 至少需要 20 个交易日才能产出第一条指标，
      // 后端 quotes 默认仅 90 天会让图表前 1/4 出现空白；扩到一年既覆盖指标起步，又能完整看走势
      const startDate = new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
      const [q, ind] = await Promise.all([
        postQuotes({ stockCode, startDate }),
        postIndicators({ stockCode }),
      ]);
      setQuotes(q.data ?? []);
      // 指标 API 按 trade_date DESC 返回，图表 X 轴需要升序（时间从左到右）
      setIndicators((ind.data ?? []).slice().reverse());
    } catch {
      // 错误已由 fetch 封装处理
    } finally {
      setLoading(false);
    }
  }, [stockCode]);

  const option = useMemo(() => buildOption(quotes, indicators), [quotes, indicators]);

  useEffect(() => {
    if (open && stockCode) {
      loadData();
    } else {
      setQuotes([]);
      setIndicators([]);
      setFullscreen(false);
      setDrawerSize(1400);
    }
  }, [open, stockCode, loadData]);

  // 全屏时图表撑满抽屉内容区（减去标题+底部操作栏高度），普通模式给更充裕的高度
  const chartHeight = fullscreen ? 'calc(100vh - 140px)' : '720px';

  return (
    <Drawer
      title={titleNode}
      open={open}
      onCancel={onCancel}
      size={drawerSize}
      destroyOnHidden
      showOkButton={false}
      footer={null}
    >
      <Spin spinning={loading}>
        {indicators.length > 0 ? (
          <ReactECharts option={option} style={{ height: chartHeight }} notMerge lazyUpdate />
        ) : (
          // 后端历史数据不足或未跑过 DAILY_PIPELINE 时给个明确提示，避免页面空白
          <div style={{ height: chartHeight }} className="flex items-center justify-center">
            <Empty
              description={
                loading ? '加载中…' : '暂无行情或指标数据，请先运行 QUOTE_SYNC + DAILY_PIPELINE'
              }
            />
          </div>
        )}
      </Spin>
    </Drawer>
  );
};

export default StockChartDrawer;
