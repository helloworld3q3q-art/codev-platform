import { ToolOutlined } from '@ant-design/icons';
import { ThoughtChain } from '@ant-design/x';
import type { ThoughtChainProps } from '@ant-design/x';
import { Collapse } from 'antd';
import { useMemo } from 'react';

// 答复末尾的"工具调用流":把本轮 steps 里真正调了工具的步骤, 折叠成 ThoughtChain 展示。
interface ToolFlowProps {
  steps: API.ChatStep[];
}

const summarize = (args: unknown): string => {
  if (args === null || args === undefined) {
    return '';
  }
  try {
    const s = typeof args === 'string' ? args : JSON.stringify(args);
    return s.length > 200 ? `${s.slice(0, 200)}…` : s;
  } catch {
    return '';
  }
};

const ToolFlow: React.FC<ToolFlowProps> = ({ steps }) => {
  const toolSteps = useMemo(() => steps.filter((s) => s.tool), [steps]);

  const chainItems = useMemo<ThoughtChainProps['items']>(
    () =>
      toolSteps.map((s, i) => {
        const argsText = summarize(s.args);
        return {
          key: String(s.n ?? i),
          status: 'success' as const,
          title: `${i + 1}. ${s.tool}`,
          description: s.thought ?? undefined,
          content: (
            <div className="text-12 break-all">
              {argsText ? <div className="text-#8c8c8c">入参: {argsText}</div> : null}
              {s.resultSummary ? <div className="mt-2">{s.resultSummary}</div> : null}
            </div>
          ),
        };
      }),
    [toolSteps],
  );

  if (!toolSteps.length) {
    return null;
  }

  const collapseItems = [
    {
      key: 'tools',
      label: (
        <span className="text-12 text-#8c8c8c">
          <ToolOutlined /> 调用了 {toolSteps.length} 个工具
        </span>
      ),
      children: <ThoughtChain items={chainItems} line="dashed" />,
    },
  ];

  return <Collapse ghost size="small" items={collapseItems} className="mt-4" />;
};

export default ToolFlow;
