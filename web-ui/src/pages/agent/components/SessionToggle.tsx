import { MessageOutlined } from '@ant-design/icons';
import { useCallback, useRef, useState } from 'react';

// 会话开关:贴左边缘(菜单右侧)的竖向小钮, 可上下拖动(位置持久化), 点击开/关会话面板。
// 区分点击与拖动(位移阈值): 拖动只挪位置, 未拖动才视为点击。

const CARD_H = 720; // 与 index.tsx 卡片 h-720 对齐
const TAB_H = 72;
const THRESHOLD = 4;

const clamp = (v: number, min: number, max: number): number => {
  return Math.min(Math.max(v, min), max);
};

const readY = (): number | null => {
  try {
    const raw = localStorage.getItem('agent-session-tab-y');
    return raw === null ? null : Number(raw);
  } catch {
    return null;
  }
};

interface SessionToggleProps {
  onToggle: () => void;
}

const SessionToggle: React.FC<SessionToggleProps> = ({ onToggle }) => {
  const [y, setY] = useState<number>(() => {
    const saved = readY();
    return clamp(saved ?? (CARD_H - TAB_H) / 2, 4, CARD_H - TAB_H);
  });
  const yRef = useRef<number>(y);
  yRef.current = y;
  const dragRef = useRef<{ sy: number; by: number; moved: boolean } | null>(null);

  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>): void => {
    dragRef.current = { sy: e.clientY, by: yRef.current, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>): void => {
    const d = dragRef.current;
    if (!d) {
      return;
    }
    const dy = e.clientY - d.sy;
    if (!d.moved && Math.abs(dy) < THRESHOLD) {
      return;
    }
    d.moved = true;
    setY(clamp(d.by + dy, 4, CARD_H - TAB_H));
  }, []);

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>): void => {
      const d = dragRef.current;
      if (!d) {
        return;
      }
      dragRef.current = null;
      e.currentTarget.releasePointerCapture(e.pointerId);
      if (d.moved) {
        try {
          localStorage.setItem('agent-session-tab-y', String(yRef.current));
        } catch {
          // 忽略持久化失败
        }
      } else {
        onToggle(); // 未拖动 = 点击 → 开/关面板
      }
    },
    [onToggle],
  );

  return (
    <div
      className="absolute left-0 z-20 flex items-center justify-center w-18 bg-primaryHover text-#ffffff rounded-r-8 shadow-md cursor-grab select-none touch-none active:cursor-grabbing"
      style={{ top: y, height: TAB_H }}
      title="会话(点击开/关, 可上下拖动)"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
    >
      <MessageOutlined className="text-14" />
    </div>
  );
};

export default SessionToggle;
