// 通用可拖动浮层 —— 标题栏作拖拽手柄, position:fixed 经 portal 挂 body, 浮于全局之上。
// 位置可选持久化到 localStorage(storageKey)。无外部拖拽依赖, 纯 Pointer Events 实现。
import { CloseOutlined, HolderOutlined } from '@ant-design/icons';
import { useCallback, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

interface Position {
  x: number;
  y: number;
}

interface DraggablePanelProps {
  open: boolean;
  title: React.ReactNode;
  children: React.ReactNode;
  onClose?: () => void;
  width?: number;
  defaultPosition?: Position;
  storageKey?: string;
}

const clamp = (v: number, min: number, max: number): number => {
  return Math.min(Math.max(v, min), max);
};

const readPos = (key?: string): Position | null => {
  if (!key) {
    return null;
  }
  try {
    const raw = localStorage.getItem(`dragpanel:${key}`);
    return raw ? (JSON.parse(raw) as Position) : null;
  } catch {
    return null;
  }
};

const initPos = (saved: Position | null, fallback?: Position): Position => {
  const base = saved ?? fallback ?? { x: 32, y: 120 };
  if (typeof window === 'undefined') {
    return base;
  }
  return { x: clamp(base.x, 0, window.innerWidth - 80), y: clamp(base.y, 0, window.innerHeight - 40) };
};

const DraggablePanel: React.FC<DraggablePanelProps> = ({
  open,
  title,
  children,
  onClose,
  width = 280,
  defaultPosition,
  storageKey,
}) => {
  const [pos, setPos] = useState<Position>(() => initPos(readPos(storageKey), defaultPosition));
  const posRef = useRef<Position>(pos);
  posRef.current = pos;
  const dragRef = useRef<{ sx: number; sy: number; bx: number; by: number } | null>(null);

  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>): void => {
    dragRef.current = { sx: e.clientX, sy: e.clientY, bx: posRef.current.x, by: posRef.current.y };
    e.currentTarget.setPointerCapture(e.pointerId);
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>): void => {
    const d = dragRef.current;
    if (!d) {
      return;
    }
    const nx = clamp(d.bx + (e.clientX - d.sx), 0, window.innerWidth - 80);
    const ny = clamp(d.by + (e.clientY - d.sy), 0, window.innerHeight - 40);
    setPos({ x: nx, y: ny });
  }, []);

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>): void => {
      if (!dragRef.current) {
        return;
      }
      dragRef.current = null;
      e.currentTarget.releasePointerCapture(e.pointerId);
      if (storageKey) {
        try {
          localStorage.setItem(`dragpanel:${storageKey}`, JSON.stringify(posRef.current));
        } catch {
          // 忽略持久化失败(隐私模式等)
        }
      }
    },
    [storageKey],
  );

  if (!open) {
    return null;
  }

  return createPortal(
    <div
      className="fixed z-1000 flex flex-col bg-#ffffff rounded-8 border border-#e8e8e8 shadow-lg"
      style={{ left: pos.x, top: pos.y, width }}
    >
      <div
        className="flex items-center justify-between px-12 py-8 border-b border-#f0f0f0 cursor-move select-none touch-none"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        <span className="flex items-center gap-6 text-13 font-500">
          <HolderOutlined className="text-#bfbfbf" />
          {title}
        </span>
        {onClose ? (
          <CloseOutlined
            className="text-12 text-#8c8c8c cursor-pointer hover:text-#595959"
            onClick={onClose}
          />
        ) : null}
      </div>
      <div className="p-12 overflow-auto" style={{ maxHeight: '64vh' }}>
        {children}
      </div>
    </div>,
    document.body,
  );
};

export default DraggablePanel;
