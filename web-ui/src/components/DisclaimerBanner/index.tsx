import { ExclamationCircleOutlined } from '@ant-design/icons';
import { Alert } from 'antd';
import { useMemo } from 'react';

export type DisclaimerScenario = 'research' | 'backtest' | 'realtime' | 'governance';

interface DisclaimerBannerProps {
  scenario: DisclaimerScenario;
  className?: string;
  showIcon?: boolean;
}

interface DisclaimerFooterProps {
  /**
   * 优先回读后端推荐快照中的 disclaimer_snapshot.disclaimer 数组；
   * 缺失时降级到 DISCLAIMER_TEXT[scenario] 兜底文案。
   * 参考：.claude/rules/snapshot-trio-write.md §7 反例 2
   */
  disclaimerLines?: string[];
  scenario?: DisclaimerScenario;
  className?: string;
}

// 合规免责文案，来源：docs/architecture/roadmap-2026-05-11/compliance-audit-runbook.md §四
const DISCLAIMER_TEXT: Record<DisclaimerScenario, { title: string; description: string }> = {
  research: {
    title: '⚠️ 研究观察用途',
    description:
      '本页面展示研究性数据（影子规则、IC 观测），不进入实盘 buy_list，仅供策略验证使用。',
  },
  backtest: {
    title: '⚠️ 历史回测结果',
    description:
      '历史回测表现不代表未来收益。本结果基于历史行情模拟撮合，存在样本偏差和滑点近似。不构成任何投资建议。',
  },
  realtime: {
    title: '⚠️ 风险提示',
    description:
      '推荐结果基于公开市场数据 + 量化规则生成，仅供参考。投资有风险，入市需谨慎。请根据个人风险承受能力独立决策。',
  },
  governance: {
    title: '⚠️ 样本治理数据',
    description:
      '本页面展示样本累积进度和 Wilson 统计区间，是内部金融逻辑解锁的决策依据。CLOSED 样本不足时所有 Track 4/5 操作均禁用。',
  },
};

/**
 * 合规免责声明横幅，按场景展示对应文案。
 * 文案来源：docs/architecture/roadmap-2026-05-11/compliance-audit-runbook.md §四。
 */
const DisclaimerBanner: React.FC<DisclaimerBannerProps> = ({
  scenario,
  className,
  showIcon = true,
}) => {
  const { title, description } = useMemo(() => {
    return DISCLAIMER_TEXT[scenario];
  }, [scenario]);

  return (
    <Alert
      type="warning"
      showIcon={showIcon}
      icon={showIcon ? <ExclamationCircleOutlined /> : undefined}
      title={title}
      description={description}
      className={className}
      classNames={{ root: 'i:mb-10' }}
    />
  );
};

/**
 * 脚注样式合规免责（轻量灰字），用于卡片底部 / 表单底部。
 * 优先回读传入的 disclaimerLines（来自后端 disclaimer_snapshot），
 * 缺失时降级到 DISCLAIMER_TEXT[scenario].description 兜底文案。
 */
export const DisclaimerFooter: React.FC<DisclaimerFooterProps> = ({
  disclaimerLines,
  scenario = 'realtime',
  className,
}) => {
  const text = useMemo(() => {
    if (disclaimerLines && disclaimerLines.length > 0) {
      return disclaimerLines.join(' ');
    }
    return DISCLAIMER_TEXT[scenario].description;
  }, [disclaimerLines, scenario]);

  return <div className={className ?? 'text-12 text-#bfbfbf mt-4'}>以上为参考数据。{text}</div>;
};

export default DisclaimerBanner;
