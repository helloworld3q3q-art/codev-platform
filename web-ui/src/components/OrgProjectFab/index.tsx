// 组织/项目 悬浮入口 —— 右下角可拖动小圆钮, 点击开/关 组织/项目 浮窗。
// 圆钮自身可随意拖动(位置持久化), 区分"点击"与"拖动"(位移阈值)。全局挂载(TabContainer)。
import { ClusterOutlined } from '@ant-design/icons';
import { useModel } from '@umijs/max';
import { useCallback, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import DraggablePanel from '@/components/DraggablePanel';
import OrgSelect from '@/components/Form/Select/OrgSelect';
import ProjectSelect from '@/components/Form/Select/ProjectSelect';

interface Position {
  x: number;
  y: number;
}

const FAB_SIZE = 48;
const DRAG_THRESHOLD = 4; // 位移 < 4px 视为点击而非拖动

const clamp = (v: number, min: number, max: number): number => {
  return Math.min(Math.max(v, min), max);
};

const readFabPos = (): Position | null => {
  try {
    const raw = localStorage.getItem('dragfab:org-project');
    return raw ? (JSON.parse(raw) as Position) : null;
  } catch {
    return null;
  }
};

const initFabPos = (): Position => {
  const saved = readFabPos();
  if (typeof window === 'undefined') {
    return saved ?? { x: 24, y: 24 };
  }
  const base = saved ?? { x: window.innerWidth - FAB_SIZE - 24, y: window.innerHeight - FAB_SIZE - 24 };
  return { x: clamp(base.x, 0, window.innerWidth - FAB_SIZE), y: clamp(base.y, 0, window.innerHeight - FAB_SIZE) };
};

const panelDefaultPos = (): Position => {
  if (typeof window === 'undefined') {
    return { x: 600, y: 300 };
  }
  return { x: window.innerWidth - 300, y: Math.max(64, window.innerHeight - 380) };
};

// select 弹层渲染进面板内(而非 body), 拖动浮窗时跟随、不残留。
const getPanelPopupContainer = (node: HTMLElement): HTMLElement => {
  return node.parentElement ?? document.body;
};

const OrgProjectFab: React.FC = () => {
  const { currentOrgId } = useModel('org');
  const [pos, setPos] = useState<Position>(initFabPos);
  const [open, setOpen] = useState<boolean>(false);
  const posRef = useRef<Position>(pos);
  posRef.current = pos;
  const dragRef = useRef<{ sx: number; sy: number; bx: number; by: number; moved: boolean } | null>(null);

  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>): void => {
    dragRef.current = { sx: e.clientX, sy: e.clientY, bx: posRef.current.x, by: posRef.current.y, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>): void => {
    const d = dragRef.current;
    if (!d) {
      return;
    }
    const dx = e.clientX - d.sx;
    const dy = e.clientY - d.sy;
    if (!d.moved && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) {
      return;
    }
    d.moved = true;
    setPos({
      x: clamp(d.bx + dx, 0, window.innerWidth - FAB_SIZE),
      y: clamp(d.by + dy, 0, window.innerHeight - FAB_SIZE),
    });
  }, []);

  const handlePointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>): void => {
    const d = dragRef.current;
    if (!d) {
      return;
    }
    dragRef.current = null;
    e.currentTarget.releasePointerCapture(e.pointerId);
    if (d.moved) {
      try {
        localStorage.setItem('dragfab:org-project', JSON.stringify(posRef.current));
      } catch {
        // 忽略持久化失败
      }
    } else {
      setOpen((v) => !v); // 未拖动 = 点击 → 开/关面板
    }
  }, []);

  const closePanel = useCallback((): void => setOpen(false), []);

  return (
    <>
      {createPortal(
        <div
          className="fixed z-1000 flex items-center justify-center rounded-full bg-primaryHover text-#ffffff shadow-lg cursor-grab select-none touch-none active:cursor-grabbing"
          style={{ left: pos.x, top: pos.y, width: FAB_SIZE, height: FAB_SIZE }}
          title="组织 / 项目"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
        >
          <ClusterOutlined className="text-20" />
        </div>,
        document.body,
      )}
      <DraggablePanel
        open={open}
        onClose={closePanel}
        title="组织 / 项目"
        width={260}
        defaultPosition={panelDefaultPos()}
        storageKey="org-project"
      >
        <div className="mb-12">
          <div className="mb-4 text-12 text-#8c8c8c">组织</div>
          <OrgSelect getPopupContainer={getPanelPopupContainer} />
        </div>
        <div>
          <div className="mb-4 text-12 text-#8c8c8c">项目</div>
          <ProjectSelect key={currentOrgId} getPopupContainer={getPanelPopupContainer} />
        </div>
      </DraggablePanel>
    </>
  );
};

export default OrgProjectFab;
