/**
 * 枚举数据文件 - 自动生成，实际业务从接口获取，生成枚举文件只为展示，不做实际业务取值，或者编程取值
 * 注意：此文件仅用于展示枚举数据，不应该直接在业务代码中使用。
 * 提供给AI识别业务，用于生成业务代码。
 * 自动生成于: 2026-05-26T05:33:27.275Z
 * 请勿手动修改此文件
 */


// 枚举项类型定义
export interface EnumItem {
  value: string;
  label: string;
  order: number;
  description?: string;
}

// 枚举组类型定义
export interface EnumGroup {
  [enumType: string]: EnumItem[];
}


// 枚举数据
const ENUMS_LOCAL: EnumGroup = {
  DataSourceEnum: [
    {
      value: "AKSHARE",
      label: "AkShare",
      order: 1,
      description: "DataSourceEnum - AkShare"
    },
    {
      value: "BAOSTOCK",
      label: "Baostock",
      order: 2,
      description: "DataSourceEnum - Baostock"
    },
    {
      value: "YAHOO",
      label: "Yahoo Finance",
      order: 3,
      description: "DataSourceEnum - Yahoo Finance"
    },
    {
      value: "SIMULATED",
      label: "模拟数据",
      order: 4,
      description: "DataSourceEnum - 模拟数据"
    }
  ],
  JobTypeEnum: [
    {
      value: "DAILY_PIPELINE",
      label: "每日完整分析管道",
      order: 1,
      description: "JobTypeEnum - 每日完整分析管道"
    },
    {
      value: "QUOTE_SYNC",
      label: "行情数据同步",
      order: 2,
      description: "JobTypeEnum - 行情数据同步"
    },
    {
      value: "ENRICHMENT_SYNC",
      label: "估值/资金流同步",
      order: 3,
      description: "JobTypeEnum - 估值/资金流同步"
    },
    {
      value: "RECOMMEND_REFRESH",
      label: "推荐结果刷新",
      order: 4,
      description: "JobTypeEnum - 推荐结果刷新"
    },
    {
      value: "REPORT_GENERATE",
      label: "报告生成",
      order: 5,
      description: "JobTypeEnum - 报告生成"
    },
    {
      value: "DIAGNOSTICS",
      label: "诊断分析",
      order: 6,
      description: "JobTypeEnum - 诊断分析"
    },
    {
      value: "SIGNAL_VALIDATION",
      label: "信号回验",
      order: 7,
      description: "JobTypeEnum - 信号回验"
    },
    {
      value: "IMPORT_LEGACY_CONFIG",
      label: "导入旧配置",
      order: 8,
      description: "JobTypeEnum - 导入旧配置"
    },
    {
      value: "NORTH_BOUND_SYNC",
      label: "北向资金同步（已弃用 2026-05-15）",
      order: 9,
      description: "JobTypeEnum - 北向资金同步（已弃用 2026-05-15）"
    },
    {
      value: "LHB_SYNC",
      label: "龙虎榜同步",
      order: 10,
      description: "JobTypeEnum - 龙虎榜同步"
    },
    {
      value: "EARNINGS_FORECAST_SYNC",
      label: "业绩预告同步",
      order: 11,
      description: "JobTypeEnum - 业绩预告同步"
    },
    {
      value: "RECOMMENDATION_PNL_SYNC",
      label: "推荐 PnL 追踪同步",
      order: 12,
      description: "JobTypeEnum - 推荐 PnL 追踪同步"
    },
    {
      value: "FUND_FLOW_SYNC",
      label: "资金流同步",
      order: 13,
      description: "JobTypeEnum - 资金流同步"
    },
    {
      value: "FINANCIAL_SYNC",
      label: "财务因子同步",
      order: 14,
      description: "JobTypeEnum - 财务因子同步"
    },
    {
      value: "INTRADAY_SESSION_SYNC",
      label: "盘中分段同步",
      order: 15,
      description: "JobTypeEnum - 盘中分段同步"
    },
    {
      value: "BACKTEST_RUN",
      label: "策略回测运行",
      order: 16,
      description: "JobTypeEnum - 策略回测运行"
    },
    {
      value: "RECOMMENDATION_TRACK_REFRESH",
      label: "推荐追踪定时刷新",
      order: 17,
      description: "JobTypeEnum - 推荐追踪定时刷新"
    },
    {
      value: "MARGIN_TRADING_SYNC",
      label: "融资融券同步",
      order: 18,
      description: "JobTypeEnum - 融资融券同步"
    },
    {
      value: "STOCK_INFO_LIQUIDITY_REFRESH",
      label: "股票流动性字段同步",
      order: 19,
      description: "JobTypeEnum - 股票流动性字段同步"
    }
  ],
  TradePlanStatusEnum: [
    {
      value: "DRAFT",
      label: "草稿",
      order: 1,
      description: "TradePlanStatusEnum - 草稿"
    },
    {
      value: "ACTIVE",
      label: "已激活",
      order: 2,
      description: "TradePlanStatusEnum - 已激活"
    },
    {
      value: "CLOSED",
      label: "已关闭",
      order: 3,
      description: "TradePlanStatusEnum - 已关闭"
    }
  ],
  RuleCodeEnum: [
    {
      value: "DATA_QUALITY",
      label: "数据质量评分",
      order: 1,
      description: "对该股票当日数据完整性 / 时效性 / 异常值的综合评分。评分过低（缺关键字段、停牌长、数据滞后）会直接降级或排除推荐，是其他所有规则的前置门槛。"
    },
    {
      value: "MACD_CROSS",
      label: "MACD 金叉死叉",
      order: 2,
      description: "MACD 指数平滑异同移动平均线交叉信号。金叉（DIF 上穿 DEA）为看多入场信号，死叉相反。趋势市胜率较高，震荡市易出假信号，需结合成交量与均线方向过滤。"
    },
    {
      value: "RSI_LEVEL",
      label: "RSI 强弱指数",
      order: 3,
      description: "14 日 RSI 相对强弱指数。RSI < 30 为超卖区，反弹概率上升；RSI > 70 为超买区，回调概率上升。A 股波动大，单独使用胜率有限，需配合趋势方向与成交量过滤假突破。"
    },
    {
      value: "MA_TREND",
      label: "均线趋势",
      order: 4,
      description: "MA5 / MA10 / MA20 多周期均线方向与排列判断。多头排列（短期上、中期中、长期下）确认上升趋势；空头排列反之。趋势确立后顺势操作胜率显著高于逆势。"
    },
    {
      value: "BOLL_POSITION",
      label: "布林带位置",
      order: 5,
      description: "20 日布林带（中轨 ±2σ）位置信号。价格触下轨偏多反弹，触上轨警惕回调；带宽收窄预示变盘，带宽急扩配合方向选择是强趋势信号。"
    },
    {
      value: "VOLATILITY_20D",
      label: "20 日波动率",
      order: 6,
      description: "近 20 日收益率标准差（日波动，非年化）。高波动股票适合短线博弈但风险高，低波动股票适合中线持有。仓位管理与止损宽度需按波动率自适应。"
    },
    {
      value: "VALUATION_LEVEL",
      label: "估值水位",
      order: 7,
      description: "PE / PB / PS 综合分位估值水位。低估值（行业历史 30% 分位以下）配合景气复苏胜率高；高估值（80% 分位以上）需警惕戴维斯双杀，仅在强动量催化下短线参与。"
    },
    {
      value: "MAIN_FUND_FLOW",
      label: "主力资金流",
      order: 8,
      description: "通过逐笔分单识别的大单 / 特大单净流入信号。连续多日主力净流入是建仓特征，单日异常流入需警惕诱多。需结合龙虎榜与北向数据交叉验证。"
    },
    {
      value: "ROE_QUALITY",
      label: "ROE 盈利质量",
      order: 9,
      description: "净资产收益率（ROE）质量评估。连续多年 ROE > 15% 且稳定的公司具备复利能力，是中长线选股核心指标。需排除一次性收益与高杠杆推高的虚高 ROE。"
    },
    {
      value: "DEBT_RISK",
      label: "负债风险",
      order: 10,
      description: "资产负债率 / 流动比 / 利息覆盖等综合偿债风险。高负债叠加业绩下滑是戴维斯双杀前兆；周期股顶部高负债是顶部信号。负债风险高的标的需大幅降权或排除。"
    },
    {
      value: "GROWTH_MOMENTUM",
      label: "成长动能",
      order: 11,
      description: "营收 / 净利润��比增速与环比加速度。营收净利双高增（>30%）且环比加速是强成长信号；增速由高转低预示成长瓶颈，PEG 估值切换不利。"
    },
    {
      value: "GROSS_MARGIN_QUALITY",
      label: "毛利率质量",
      order: 12,
      description: "毛利率水平与同比变化。高毛利（>40%）且稳定反映护城河；毛利率持续下滑预示竞争加剧或成本压力，是基本面恶化前置信号。"
    },
    {
      value: "A_SHARE_LIMIT_ST",
      label: "A 股涨停 / ST 风险",
      order: 13,
      description: "A 股特色合规规则：涨跌停板触及无法成交、ST / *ST 标的退市风险高、近 N 日触板异常。命中时直接排除推荐或触发 HIGH 风险等级警示。"
    },
    {
      value: "VOLUME_CONFIRM",
      label: "量价确认",
      order: 14,
      description: "突破 / 反转信号的成交量确认。放量突破（量比 > 1.5）有效性显著高于缩量突破；缩量回踩支撑是低吸时机。无量上涨易诱多，无量下跌易诱空。"
    },
    {
      value: "MACD_MOMENTUM",
      label: "MACD 柱量动能",
      order: 15,
      description: "MACD 柱状值（DIF - DEA）的扩张 / 收缩动能。柱状由负转正且持续放大是强多头动能；连续 3 根缩短预示动能衰竭，配合金叉死叉提升信号质量。"
    },
    {
      value: "KDJ_SIGNAL",
      label: "KDJ 超买超卖",
      order: 16,
      description: "9 日 KDJ 随机指标。J 值 < 0 极度超卖反弹概率高；J 值 > 100 极度超买回调概率高。KDJ 在震荡市效果好，单边趋势市易钝化失效。"
    },
    {
      value: "MA_FULL_ALIGN",
      label: "均线完全多头排列",
      order: 17,
      description: "MA5 > MA10 > MA20 > MA60 完全多头排列，是中期趋势确立的强信号。完全排列形成后回踩 MA10 / MA20 是高胜率加仓位置。空头完全排列反之。"
    },
    {
      value: "TRAILING_STOP",
      label: "追踪止损",
      order: 18,
      description: "基于 ATR 或固定百分比的追踪止损信号。价格创新高后回撤超过追踪阈值触发止盈出场，锁定上涨利润。是趋势跟随策略的核心退出规则。"
    },
    {
      value: "NEW_HIGH_BREAKOUT",
      label: "新高突破",
      order: 19,
      description: "突破 N 日 / 历史新高的趋势加速信号。配合放量与板块共振胜率高；无量假突破易回落原区间。海龟交易法核心信号之一。"
    },
    {
      value: "NORTH_INFLOW",
      label: "北向连续净流入（已废弃 p1.14）",
      order: 20,
      description: "已废弃（2026-05-15）。原规则识别北向资金连续多日净增持。监管层 2024-08 起停止披露个股日度持股数据 + fetcher 错配问题，三规则统一下线。仅保留枚举值兼容历史数据。"
    },
    {
      value: "NORTH_OUTFLOW",
      label: "北向连续净流出（已废弃 p1.14）",
      order: 21,
      description: "已废弃（2026-05-15）。同 NORTH_INFLOW 下线原因。M5/M6 将用融资融券 + 龙虎榜 + ETF + 公募季报四源重建机构资金画像。"
    },
    {
      value: "NORTH_HOLDING_HIGH",
      label: "北向高持股占比（已废弃 p1.14）",
      order: 22,
      description: "已废弃（2026-05-15）。同 NORTH_INFLOW 下线原因。"
    },
    {
      value: "MULTI_TIMEFRAME_RESONANCE",
      label: "多周期共振",
      order: 23,
      description: "日线 / 周线 / 月线多个时间维度同向信号共振。三周期共振（如周线 MACD 金叉 + 日线突破 + 月线均线上行）是高胜率信号，但出现频次低。"
    },
    {
      value: "CHIPS_BREAKOUT",
      label: "筹码突破压力位",
      order: 24,
      description: "筹码分布（成本分布密度）显示价格突破上方密集成交区压力位。突破后原压力变支撑，配合放量是强势上行启动信号。"
    },
    {
      value: "CHIPS_SUPPORT",
      label: "筹码支撑位附近",
      order: 25,
      description: "价格回踩至筹码密集成交区下沿支撑。该区域累积大量持仓成本，多头护盘动力强，是低吸性价比高的位置。"
    },
    {
      value: "CHIPS_HEAVY_PROFIT",
      label: "获利盘过重",
      order: 26,
      description: "筹码分布显示获利盘比例过高（>85%）。意味着多数持仓已浮盈，存在集中兑现压力，警惕诱多后的获利回吐。"
    },
    {
      value: "MAIN_FUND_FOLLOW",
      label: "主力资金跟随",
      order: 27,
      description: "识别游资 / 机构席位的连续大额买入并跟随建仓。跟随窗口短（通常 T+1 ~ T+3），需严格止损控制；适合短线博弈，不构成中线持仓理由。"
    },
    {
      value: "LHB_INSTITUTION",
      label: "龙虎榜机构席位",
      order: 28,
      description: "龙虎榜买方出现知名机构 / 游资席位。机构净买入是中线认可信号；游资席位往往波动剧烈，需结合个股基本面与持仓周期判断。"
    },
    {
      value: "EARNINGS_BEAT_FORECAST",
      label: "业绩超预期",
      order: 29,
      description: "实际业绩快报 / 预告超出市场一致预期。超预期幅度越大、行业景气共振越强，股价正向反应越持久。需警惕一次性损益推高的虚假超预期。"
    },
    {
      value: "INDUSTRY_BOOM",
      label: "行业景气度高",
      order: 30,
      description: "所属行业景气度评分进入前 20% 分位。强景气行业的个股普涨概率高（beta 效应），是自上而下选股的核心过滤条件之一。"
    },
    {
      value: "INDUSTRY_ROTATION",
      label: "行业轮动信号",
      order: 31,
      description: "行业资金 / 涨幅相对强弱排名变化识别轮动方向。从滞涨低位行业切换到启动初期行业胜率高；追高已涨幅靠前行业风险大，需配合估值过滤。"
    },
    {
      value: "MARKET_TREND_BULL",
      label: "大盘多头趋势（已废弃 p1.13）",
      order: 32,
      description: "已废弃（p1.13）。原规则识别大盘多头环境给所有股票统一加分，但不影响相对排名且与 RecommendationEngine._REGIME_OVERRIDES 重复。系统级仓位 / 现金 / 行业上限由推荐引擎覆写完成。仅保留枚举值兼容历史数据。"
    },
    {
      value: "MARKET_TREND_BEAR",
      label: "大盘空头趋势（已废弃 p1.13）",
      order: 33,
      description: "已废弃（p1.13）。同 MARKET_TREND_BULL 下线原因。"
    }
  ],
  AlertTypeEnum: [
    {
      value: "RISK_CHANGED",
      label: "风险升高",
      order: 1,
      description: "AlertTypeEnum - 风险升高"
    },
    {
      value: "SIGNAL_CHANGED",
      label: "信号变化",
      order: 2,
      description: "AlertTypeEnum - 信号变化"
    },
    {
      value: "DATA_MISSING",
      label: "数据缺失",
      order: 3,
      description: "AlertTypeEnum - 数据缺失"
    },
    {
      value: "MAX_DRAWDOWN_HIT",
      label: "信号回撤超阈",
      order: 4,
      description: "AlertTypeEnum - 信号回撤超阈"
    },
    {
      value: "RULE_DECAY",
      label: "规则失效预警",
      order: 5,
      description: "AlertTypeEnum - 规则失效预警"
    },
    {
      value: "RULE_UNDERPERFORMANCE",
      label: "规则胜率崩塌",
      order: 6,
      description: "AlertTypeEnum - 规则胜率崩塌"
    },
    {
      value: "DATA_INTEGRITY_GAP",
      label: "数据完整性告警",
      order: 7,
      description: "AlertTypeEnum - 数据完整性告警"
    },
    {
      value: "LIQUIDITY_COVERAGE_LOW",
      label: "流动性覆盖不足",
      order: 8,
      description: "AlertTypeEnum - 流动性覆盖不足"
    },
    {
      value: "TRACK_SANITY_VIOLATION",
      label: "追踪逻辑异常",
      order: 9,
      description: "AlertTypeEnum - 追踪逻辑异常"
    },
    {
      value: "PIPELINE_FAILURE",
      label: "跑批失败",
      order: 10,
      description: "AlertTypeEnum - 跑批失败"
    },
    {
      value: "STALE_JOB",
      label: "任务卡住",
      order: 11,
      description: "AlertTypeEnum - 任务卡住"
    }
  ],
  TradeTimingEnum: [
    {
      value: "AFTER_TRADE_CONDITION_CONFIRMED",
      label: "交易条件确认后",
      order: 1,
      description: "TradeTimingEnum - 交易条件确认后"
    },
    {
      value: "AT_MARKET_OPEN",
      label: "开盘买入",
      order: 2,
      description: "TradeTimingEnum - 开盘买入"
    },
    {
      value: "AT_MARKET_CLOSE",
      label: "收盘卖出",
      order: 3,
      description: "TradeTimingEnum - 收盘卖出"
    },
    {
      value: "ON_PULLBACK",
      label: "回调买入",
      order: 4,
      description: "TradeTimingEnum - 回调买入"
    },
    {
      value: "ON_BREAKOUT",
      label: "突破买入",
      order: 5,
      description: "TradeTimingEnum - 突破买入"
    },
    {
      value: "ON_STOP_LOSS_TRIGGERED",
      label: "触发止损",
      order: 6,
      description: "TradeTimingEnum - 触发止损"
    },
    {
      value: "ON_TAKE_PROFIT_TRIGGERED",
      label: "触发止盈",
      order: 7,
      description: "TradeTimingEnum - 触发止盈"
    },
    {
      value: "MANUAL_DECISION",
      label: "人工决策",
      order: 8,
      description: "TradeTimingEnum - 人工决策"
    }
  ],
  FocusLevelEnum: [
    {
      value: "LOW",
      label: "低",
      order: 1,
      description: "FocusLevelEnum - 低"
    },
    {
      value: "MEDIUM",
      label: "中",
      order: 2,
      description: "FocusLevelEnum - 中"
    },
    {
      value: "HIGH",
      label: "高",
      order: 3,
      description: "FocusLevelEnum - 高"
    }
  ],
  ForecastTypeEnum: [
    {
      value: "INCREASE",
      label: "预增",
      order: 1,
      description: "ForecastTypeEnum - 预增"
    },
    {
      value: "DECREASE",
      label: "预减",
      order: 2,
      description: "ForecastTypeEnum - 预减"
    },
    {
      value: "TURN_PROFIT",
      label: "扭亏",
      order: 3,
      description: "ForecastTypeEnum - 扭亏"
    },
    {
      value: "CONTINUE_PROFIT",
      label: "续盈",
      order: 4,
      description: "ForecastTypeEnum - 续盈"
    },
    {
      value: "CONTINUE_LOSS",
      label: "续亏",
      order: 5,
      description: "ForecastTypeEnum - 续亏"
    },
    {
      value: "UNCERTAIN",
      label: "不确定",
      order: 6,
      description: "ForecastTypeEnum - 不确定"
    }
  ],
  BacktestStatusEnum: [
    {
      value: "PENDING",
      label: "待运行",
      order: 1,
      description: "BacktestStatusEnum - 待运行"
    },
    {
      value: "RUNNING",
      label: "运行中",
      order: 2,
      description: "BacktestStatusEnum - 运行中"
    },
    {
      value: "SUCCESS",
      label: "成功",
      order: 3,
      description: "BacktestStatusEnum - 成功"
    },
    {
      value: "FAILED",
      label: "失败",
      order: 4,
      description: "BacktestStatusEnum - 失败"
    },
    {
      value: "CANCELLED",
      label: "已取消",
      order: 5,
      description: "BacktestStatusEnum - 已取消"
    }
  ],
  RuleVersionEnum: [
    {
      value: "P0_2",
      label: "p0.2",
      order: 1,
      description: "RuleVersionEnum - p0.2"
    },
    {
      value: "P1_6",
      label: "p1.6",
      order: 2,
      description: "RuleVersionEnum - p1.6"
    },
    {
      value: "P1_7",
      label: "p1.7",
      order: 3,
      description: "RuleVersionEnum - p1.7"
    },
    {
      value: "P1_8",
      label: "p1.8",
      order: 4,
      description: "RuleVersionEnum - p1.8"
    }
  ],
  StockPoolTypeEnum: [
    {
      value: "WATCHLIST",
      label: "重点关注",
      order: 1,
      description: "StockPoolTypeEnum - 重点关注"
    },
    {
      value: "OPTIONAL",
      label: "自选股",
      order: 2,
      description: "StockPoolTypeEnum - 自选股"
    },
    {
      value: "SECTOR",
      label: "板块池",
      order: 3,
      description: "StockPoolTypeEnum - 板块池"
    },
    {
      value: "SYSTEM",
      label: "系统池",
      order: 4,
      description: "StockPoolTypeEnum - 系统池"
    }
  ],
  MenuTypeEnum: [
    {
      value: "CATALOG",
      label: "目录",
      order: 1,
      description: "MenuTypeEnum - 目录"
    },
    {
      value: "MENU",
      label: "菜单",
      order: 2,
      description: "MenuTypeEnum - 菜单"
    },
    {
      value: "BUTTON",
      label: "按钮",
      order: 3,
      description: "MenuTypeEnum - 按钮"
    }
  ],
  SessionPatternEnum: [
    {
      value: "GAP_UP_FADE",
      label: "高开回落",
      order: 1,
      description: "SessionPatternEnum - 高开回落"
    },
    {
      value: "GAP_DOWN_RECOVER",
      label: "低开修复",
      order: 2,
      description: "SessionPatternEnum - 低开修复"
    },
    {
      value: "AFTERNOON_PULL_UP",
      label: "午后拉升",
      order: 3,
      description: "SessionPatternEnum - 午后拉升"
    },
    {
      value: "AFTERNOON_SELL_OFF",
      label: "午后回落",
      order: 4,
      description: "SessionPatternEnum - 午后回落"
    },
    {
      value: "TAIL_ACCUMULATION",
      label: "尾盘承接",
      order: 5,
      description: "SessionPatternEnum - 尾盘承接"
    },
    {
      value: "TAIL_DISTRIBUTION",
      label: "尾盘派发",
      order: 6,
      description: "SessionPatternEnum - 尾盘派发"
    },
    {
      value: "NEUTRAL",
      label: "中性震荡",
      order: 7,
      description: "SessionPatternEnum - 中性震荡"
    }
  ],
  RiskLevelEnum: [
    {
      value: "LOW",
      label: "低风险",
      order: 1,
      description: "RiskLevelEnum - 低风险"
    },
    {
      value: "MEDIUM",
      label: "中风险",
      order: 2,
      description: "RiskLevelEnum - 中风险"
    },
    {
      value: "HIGH",
      label: "高风险",
      order: 3,
      description: "RiskLevelEnum - 高风险"
    }
  ],
  SyncStatusEnum: [
    {
      value: "PENDING",
      label: "待执行",
      order: 1,
      description: "SyncStatusEnum - 待执行"
    },
    {
      value: "RUNNING",
      label: "运行中",
      order: 2,
      description: "SyncStatusEnum - 运行中"
    },
    {
      value: "SUCCESS",
      label: "成功",
      order: 3,
      description: "SyncStatusEnum - 成功"
    },
    {
      value: "FAILED",
      label: "失败",
      order: 4,
      description: "SyncStatusEnum - 失败"
    },
    {
      value: "CANCELLED",
      label: "已取消",
      order: 5,
      description: "SyncStatusEnum - 已取消"
    },
    {
      value: "SKIPPED",
      label: "已跳过",
      order: 6,
      description: "SyncStatusEnum - 已跳过"
    }
  ],
  TradeActionEnum: [
    {
      value: "BUY",
      label: "买入",
      order: 1,
      description: "TradeActionEnum - 买入"
    },
    {
      value: "SELL",
      label: "卖出",
      order: 2,
      description: "TradeActionEnum - 卖出"
    },
    {
      value: "HOLD",
      label: "持有",
      order: 3,
      description: "TradeActionEnum - 持有"
    },
    {
      value: "BUY_CANDIDATE_BY_RATIO",
      label: "按比例建仓",
      order: 4,
      description: "TradeActionEnum - 按比例建仓"
    },
    {
      value: "BUY_ON_BREAKOUT",
      label: "突破买入",
      order: 5,
      description: "TradeActionEnum - 突破买入"
    },
    {
      value: "REDUCE_POSITION",
      label: "减仓",
      order: 6,
      description: "TradeActionEnum - 减仓"
    },
    {
      value: "CLOSE_POSITION",
      label: "清仓",
      order: 7,
      description: "TradeActionEnum - 清仓"
    },
    {
      value: "OBSERVE",
      label: "观察",
      order: 8,
      description: "TradeActionEnum - 观察"
    }
  ],
  IndustryRotationStatusEnum: [
    {
      value: "INFLOW",
      label: "资金流入",
      order: 1,
      description: "IndustryRotationStatusEnum - 资金流入"
    },
    {
      value: "OUTFLOW",
      label: "资金流出",
      order: 2,
      description: "IndustryRotationStatusEnum - 资金流出"
    },
    {
      value: "STABLE",
      label: "横盘震荡",
      order: 3,
      description: "IndustryRotationStatusEnum - 横盘震荡"
    }
  ],
  MarketRegimeEnum: [
    {
      value: "BULL_MARKET",
      label: "牛市",
      order: 1,
      description: "MarketRegimeEnum - 牛市"
    },
    {
      value: "BEAR_MARKET",
      label: "熊市",
      order: 2,
      description: "MarketRegimeEnum - 熊市"
    },
    {
      value: "DEFAULT",
      label: "默认（中性）",
      order: 3,
      description: "MarketRegimeEnum - 默认（中性）"
    }
  ],
  ExitReasonEnum: [
    {
      value: "STOP_LOSS",
      label: "触发止损",
      order: 1,
      description: "ExitReasonEnum - 触发止损"
    },
    {
      value: "TAKE_PROFIT",
      label: "触发止盈",
      order: 2,
      description: "ExitReasonEnum - 触发止盈"
    },
    {
      value: "HOLD_PERIOD",
      label: "持有期满",
      order: 3,
      description: "ExitReasonEnum - 持有期满"
    },
    {
      value: "SIGNAL",
      label: "信号反转",
      order: 4,
      description: "ExitReasonEnum - 信号反转"
    },
    {
      value: "FORCE_CLOSE",
      label: "回测末日强平",
      order: 5,
      description: "ExitReasonEnum - 回测末日强平"
    }
  ],
  TrackExitReasonEnum: [
    {
      value: "HOLD_PERIOD",
      label: "持有期满",
      order: 1,
      description: "TrackExitReasonEnum - 持有期满"
    },
    {
      value: "STOP_LOSS",
      label: "触发止损",
      order: 2,
      description: "TrackExitReasonEnum - 触发止损"
    },
    {
      value: "TAKE_PROFIT",
      label: "触发止盈",
      order: 3,
      description: "TrackExitReasonEnum - 触发止盈"
    },
    {
      value: "MANUAL",
      label: "手动关闭",
      order: 4,
      description: "TrackExitReasonEnum - 手动关闭"
    }
  ],
  UserStatusEnum: [
    {
      value: "ACTIVE",
      label: "正常",
      order: 1,
      description: "UserStatusEnum - 正常"
    },
    {
      value: "DISABLED",
      label: "已禁用",
      order: 2,
      description: "UserStatusEnum - 已禁用"
    },
    {
      value: "LOCKED",
      label: "已锁定",
      order: 3,
      description: "UserStatusEnum - 已锁定"
    }
  ],
  StockMarketEnum: [
    {
      value: "SH",
      label: "上海",
      order: 1,
      description: "StockMarketEnum - 上海"
    },
    {
      value: "SZ",
      label: "深圳",
      order: 2,
      description: "StockMarketEnum - 深圳"
    },
    {
      value: "BJ",
      label: "北京",
      order: 3,
      description: "StockMarketEnum - 北京"
    },
    {
      value: "HK",
      label: "香港",
      order: 4,
      description: "StockMarketEnum - 香港"
    },
    {
      value: "US",
      label: "美国",
      order: 5,
      description: "StockMarketEnum - 美国"
    }
  ],
  ExitReasonCategoryEnum: [
    {
      value: "STOP_LOSS",
      label: "止损",
      order: 1,
      description: "ExitReasonCategoryEnum - 止损"
    },
    {
      value: "TAKE_PROFIT",
      label: "止盈",
      order: 2,
      description: "ExitReasonCategoryEnum - 止盈"
    },
    {
      value: "TIME_EXIT",
      label: "时间退出",
      order: 3,
      description: "ExitReasonCategoryEnum - 时间退出"
    },
    {
      value: "SIGNAL_REVERSAL",
      label: "信号反转",
      order: 4,
      description: "ExitReasonCategoryEnum - 信号反转"
    },
    {
      value: "MAX_DRAWDOWN",
      label: "最大回撤",
      order: 5,
      description: "ExitReasonCategoryEnum - 最大回撤"
    }
  ],
  AlertSeverityEnum: [
    {
      value: "INFO",
      label: "信息",
      order: 1,
      description: "AlertSeverityEnum - 信息"
    },
    {
      value: "WARN",
      label: "警告",
      order: 2,
      description: "AlertSeverityEnum - 警告"
    },
    {
      value: "CRITICAL",
      label: "严重",
      order: 3,
      description: "AlertSeverityEnum - 严重"
    }
  ],
  PositionAdviceEnum: [
    {
      value: "INCREASE",
      label: "加仓",
      order: 1,
      description: "PositionAdviceEnum - 加仓"
    },
    {
      value: "KEEP",
      label: "保持",
      order: 2,
      description: "PositionAdviceEnum - 保持"
    },
    {
      value: "DECREASE",
      label: "减仓",
      order: 3,
      description: "PositionAdviceEnum - 减仓"
    },
    {
      value: "SKIP",
      label: "跳过",
      order: 4,
      description: "PositionAdviceEnum - 跳过"
    }
  ],
  TradeSideEnum: [
    {
      value: "BUY",
      label: "买入",
      order: 1,
      description: "TradeSideEnum - 买入"
    },
    {
      value: "SELL",
      label: "卖出",
      order: 2,
      description: "TradeSideEnum - 卖出"
    }
  ],
  ReportTypeEnum: [
    {
      value: "DAILY",
      label: "日报",
      order: 1,
      description: "ReportTypeEnum - 日报"
    },
    {
      value: "WEEKLY",
      label: "周报",
      order: 2,
      description: "ReportTypeEnum - 周报"
    },
    {
      value: "ATTRIBUTION",
      label: "推荐归因",
      order: 3,
      description: "ReportTypeEnum - 推荐归因"
    }
  ],
  AnalysisSignalEnum: [
    {
      value: "STRONG_BUY",
      label: "强烈买入",
      order: 1,
      description: "AnalysisSignalEnum - 强烈买入"
    },
    {
      value: "BUY",
      label: "买入",
      order: 2,
      description: "AnalysisSignalEnum - 买入"
    },
    {
      value: "HOLD",
      label: "持有",
      order: 3,
      description: "AnalysisSignalEnum - 持有"
    },
    {
      value: "WATCH",
      label: "观察",
      order: 4,
      description: "AnalysisSignalEnum - 观察"
    },
    {
      value: "SELL",
      label: "卖出",
      order: 5,
      description: "AnalysisSignalEnum - 卖出"
    },
    {
      value: "STRONG_SELL",
      label: "强烈卖出",
      order: 6,
      description: "AnalysisSignalEnum - 强烈卖出"
    }
  ],
  RecommendationTrackStatusEnum: [
    {
      value: "TRACKING",
      label: "追踪中",
      order: 1,
      description: "RecommendationTrackStatusEnum - 追踪中"
    },
    {
      value: "CLOSED",
      label: "已统计",
      order: 2,
      description: "RecommendationTrackStatusEnum - 已统计"
    }
  ],
  DataSourceCommercialStatusEnum: [
    {
      value: "FREE",
      label: "免费可商用",
      order: 1,
      description: "DataSourceCommercialStatusEnum - 免费可商用"
    },
    {
      value: "RESTRICTED",
      label: "受限（ToS 禁止商用爬取）",
      order: 2,
      description: "DataSourceCommercialStatusEnum - 受限（ToS 禁止商用爬取）"
    },
    {
      value: "UNKNOWN",
      label: "未明示（需征询官方）",
      order: 3,
      description: "DataSourceCommercialStatusEnum - 未明示（需征询官方）"
    },
    {
      value: "PAID",
      label: "商业付费授权",
      order: 4,
      description: "DataSourceCommercialStatusEnum - 商业付费授权"
    }
  ],
  DataFetcherEnum: [
    {
      value: "QUOTE",
      label: "行情(日线 + 估值)",
      order: 1,
      description: "DataFetcherEnum - 行情(日线 + 估值)"
    },
    {
      value: "FUND_FLOW",
      label: "资金流",
      order: 2,
      description: "DataFetcherEnum - 资金流"
    },
    {
      value: "LHB",
      label: "龙虎榜",
      order: 3,
      description: "DataFetcherEnum - 龙虎榜"
    },
    {
      value: "EARNINGS_FORECAST",
      label: "业绩预告",
      order: 4,
      description: "DataFetcherEnum - 业绩预告"
    },
    {
      value: "FINANCIAL",
      label: "财务因子(季度披露)",
      order: 5,
      description: "DataFetcherEnum - 财务因子(季度披露)"
    },
    {
      value: "MARGIN_TRADING",
      label: "融资融券",
      order: 6,
      description: "DataFetcherEnum - 融资融券"
    },
    {
      value: "INTRADAY",
      label: "盘中分段",
      order: 7,
      description: "DataFetcherEnum - 盘中分段"
    },
    {
      value: "MARKET_INDEX",
      label: "大盘指数",
      order: 8,
      description: "DataFetcherEnum - 大盘指数"
    },
    {
      value: "NORTH_BOUND",
      label: "北向资金(已弃用 2026-05-15)",
      order: 9,
      description: "DataFetcherEnum - 北向资金(已弃用 2026-05-15)"
    }
  ],
  AnalysisSceneEnum: [
    {
      value: "PRE_MARKET",
      label: "开盘前",
      order: 1,
      description: "AnalysisSceneEnum - 开盘前"
    },
    {
      value: "MID_DAY",
      label: "午间",
      order: 2,
      description: "AnalysisSceneEnum - 午间"
    },
    {
      value: "POST_MARKET",
      label: "收盘后",
      order: 3,
      description: "AnalysisSceneEnum - 收盘后"
    },
    {
      value: "DAILY",
      label: "日线综合",
      order: 4,
      description: "AnalysisSceneEnum - 日线综合"
    },
    {
      value: "MANUAL",
      label: "人工触发",
      order: 5,
      description: "AnalysisSceneEnum - 人工触发"
    }
  ],
  StockStatusEnum: [
    {
      value: "ACTIVE",
      label: "正常交易",
      order: 1,
      description: "StockStatusEnum - 正常交易"
    },
    {
      value: "DELISTED",
      label: "已退市",
      order: 2,
      description: "StockStatusEnum - 已退市"
    },
    {
      value: "EXCLUDED",
      label: "数据源不支持",
      order: 3,
      description: "StockStatusEnum - 数据源不支持"
    }
  ],
  LiquidityBucketEnum: [
    {
      value: "LOW_LT_50M",
      label: "< 5000 万",
      order: 1,
      description: "LiquidityBucketEnum - < 5000 万"
    },
    {
      value: "WATCH_50M_100M",
      label: "5000 万-1 亿",
      order: 2,
      description: "LiquidityBucketEnum - 5000 万-1 亿"
    },
    {
      value: "ACTIVE_GTE_100M",
      label: ">= 1 亿",
      order: 3,
      description: "LiquidityBucketEnum - >= 1 亿"
    },
    {
      value: "MISSING",
      label: "缺失",
      order: 4,
      description: "LiquidityBucketEnum - 缺失"
    }
  ]
};
export default ENUMS_LOCAL;
