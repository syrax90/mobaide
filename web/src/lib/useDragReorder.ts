/**
 * Drag-to-reorder for a short vertical list, driven by a handle per row.
 *
 * Pointer events rather than the HTML5 drag API, which most mobile browsers
 * do not implement and which paints its own ghost image: one code path then
 * serves a mouse, a finger and a pen. The rows never move in the DOM while a
 * drag runs — they are only translated — so pointer capture on the handle
 * survives to the drop, and the list is re-rendered in its new order once, by
 * the caller, from `onMove`.
 *
 * A focused handle also moves its row with ArrowUp / ArrowDown, which is the
 * keyboard and screen-reader path.
 *
 * Contract with the markup: every row is a direct child of the element given
 * `listProps`, and carries `itemProps(index)`; the handle inside it carries
 * `handleProps(index)`.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from 'react';

export type ReorderSource = 'pointer' | 'keyboard';

export interface DragReorderOptions {
  /** One stable key per row, in display order. A change cancels a running drag. */
  keys: readonly string[];
  /** Fired once per completed move — on drop, or per arrow key. */
  onMove: (from: number, to: number, source: ReorderSource) => void;
  disabled?: boolean;
}

interface DragView {
  from: number;
  over: number;
  /** How far the dragged row has travelled, page scroll included. */
  offset: number;
  /** What each sibling shifts by to close the dragged row's gap. */
  step: number;
}

interface DragSession {
  pointerId: number;
  from: number;
  keysAtStart: readonly string[];
  startY: number;
  lastY: number;
  scroller: HTMLElement;
  startScroll: number;
  centers: number[];
  step: number;
  frame: number | null;
  autoScrollFrame: number | null;
}

/** Distance from the scroller's edge at which auto-scroll starts. */
const EDGE_PX = 56;
const MAX_SCROLL_PER_FRAME = 18;
/** Pointer travel before auto-scroll may start, so a press near an edge does not scroll. */
const AUTO_SCROLL_ARM_PX = 6;

function findScroller(start: HTMLElement): HTMLElement {
  for (let node = start.parentElement; node !== null; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if (
      (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay') &&
      node.scrollHeight > node.clientHeight
    ) {
      return node;
    }
  }
  return (document.scrollingElement as HTMLElement | null) ?? document.documentElement;
}

function scrollerBounds(scroller: HTMLElement): { top: number; bottom: number } {
  if (scroller === document.scrollingElement || scroller === document.documentElement) {
    return { top: 0, bottom: window.innerHeight };
  }
  const rect = scroller.getBoundingClientRect();
  return { top: rect.top, bottom: rect.bottom };
}

function targetIndex(centers: readonly number[], from: number, offset: number): number {
  const center = (centers[from] ?? 0) + offset;
  let over = from;
  for (let j = from + 1; j < centers.length && (centers[j] ?? Infinity) < center; j += 1) over = j;
  for (let j = from - 1; j >= 0 && (centers[j] ?? -Infinity) > center; j -= 1) over = j;
  return over;
}

function offsetOf(s: DragSession): number {
  return s.lastY - s.startY + (s.scroller.scrollTop - s.startScroll);
}

function sameKeys(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((key, index) => key === b[index]);
}

export function useDragReorder({ keys, onMove, disabled = false }: DragReorderOptions) {
  const [view, setView] = useState<DragView | null>(null);
  const listRef = useRef<HTMLElement | null>(null);
  const session = useRef<DragSession | null>(null);
  const focusAfterMove = useRef<number | null>(null);
  const onMoveRef = useRef(onMove);
  useLayoutEffect(() => {
    onMoveRef.current = onMove;
  });

  const publish = useCallback((s: DragSession) => {
    const offset = offsetOf(s);
    const over = targetIndex(s.centers, s.from, offset);
    setView({ from: s.from, over, offset, step: s.step });
  }, []);

  const end = useCallback((commit: boolean) => {
    const s = session.current;
    if (s === null) return;
    session.current = null;
    if (s.frame !== null) cancelAnimationFrame(s.frame);
    if (s.autoScrollFrame !== null) cancelAnimationFrame(s.autoScrollFrame);
    setView(null);
    document.body.classList.remove('sortable-dragging');
    if (!commit) return;
    const over = targetIndex(s.centers, s.from, offsetOf(s));
    if (over !== s.from) onMoveRef.current(s.from, over, 'pointer');
  }, []);

  const autoScroll = useCallback(() => {
    const s = session.current;
    if (s === null) return;
    s.autoScrollFrame = requestAnimationFrame(autoScroll);
    if (Math.abs(s.lastY - s.startY) < AUTO_SCROLL_ARM_PX) return;
    const { top, bottom } = scrollerBounds(s.scroller);
    let delta = 0;
    if (s.lastY < top + EDGE_PX) {
      delta = -Math.ceil((MAX_SCROLL_PER_FRAME * (top + EDGE_PX - s.lastY)) / EDGE_PX);
    } else if (s.lastY > bottom - EDGE_PX) {
      delta = Math.ceil((MAX_SCROLL_PER_FRAME * (s.lastY - (bottom - EDGE_PX))) / EDGE_PX);
    }
    if (delta === 0) return;
    const before = s.scroller.scrollTop;
    s.scroller.scrollTop = before + delta;
    if (s.scroller.scrollTop !== before) publish(s);
  }, [publish]);

  // Keys changing under a drag means the list was rewritten (a push from
  // another device): the indices the drag holds no longer name the same rows.
  useEffect(() => {
    const s = session.current;
    if (s !== null && !sameKeys(s.keysAtStart, keys)) end(false);
  }, [keys, end]);

  const dragging = view !== null;
  useEffect(() => {
    if (!dragging) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') end(false);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [dragging, end]);

  useEffect(() => () => end(false), [end]);

  // A keyed re-render can move the focused handle's node, which drops focus;
  // put it back on the handle of the row that just moved.
  useLayoutEffect(() => {
    const index = focusAfterMove.current;
    if (index === null) return;
    focusAfterMove.current = null;
    listRef.current
      ?.querySelector<HTMLElement>(`:scope > [data-sortable-index="${String(index)}"] [data-sortable-handle]`)
      ?.focus();
  });

  const listProps = {
    ref: (node: HTMLElement | null) => {
      listRef.current = node;
    },
    'data-sorting': dragging,
  };

  const itemProps = (index: number) => {
    let style: CSSProperties | undefined;
    if (view !== null) {
      const { step } = view;
      if (index === view.from) {
        style = { transform: `translateY(${String(view.offset)}px)` };
      } else if (view.from < view.over && index > view.from && index <= view.over) {
        style = { transform: `translateY(${String(-step)}px)` };
      } else if (view.over < view.from && index >= view.over && index < view.from) {
        style = { transform: `translateY(${String(step)}px)` };
      }
    }
    return {
      'data-sortable-index': index,
      'data-dragging': dragging && view.from === index,
      style,
    };
  };

  const handleProps = (index: number) => ({
    'data-sortable-handle': true,
    disabled: disabled || keys.length < 2,
    onPointerDown: (event: PointerEvent<HTMLElement>) => {
      if (disabled || keys.length < 2 || session.current !== null) return;
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      const list = listRef.current;
      if (list === null) return;
      event.preventDefault();
      const rows = [...list.querySelectorAll<HTMLElement>(':scope > [data-sortable-index]')];
      const rects = rows.map((row) => row.getBoundingClientRect());
      const own = rects[index];
      const next = rects[index + 1] ?? rects[index - 1];
      if (own === undefined) return;
      // The row pitch, so variable-height siblings still close the gap exactly.
      const gap =
        next === undefined
          ? 0
          : next.top > own.top
            ? next.top - own.bottom
            : own.top - next.bottom;
      const scroller = findScroller(list);
      event.currentTarget.setPointerCapture(event.pointerId);
      session.current = {
        pointerId: event.pointerId,
        from: index,
        keysAtStart: keys,
        startY: event.clientY,
        lastY: event.clientY,
        scroller,
        startScroll: scroller.scrollTop,
        centers: rects.map((rect) => rect.top + rect.height / 2),
        step: own.height + Math.max(0, gap),
        frame: null,
        autoScrollFrame: null,
      };
      document.body.classList.add('sortable-dragging');
      setView({ from: index, over: index, offset: 0, step: session.current.step });
      session.current.autoScrollFrame = requestAnimationFrame(autoScroll);
    },
    onPointerMove: (event: PointerEvent<HTMLElement>) => {
      const s = session.current;
      if (s?.pointerId !== event.pointerId) return;
      s.lastY = event.clientY;
      if (s.frame !== null) return;
      s.frame = requestAnimationFrame(() => {
        s.frame = null;
        if (session.current === s) publish(s);
      });
    },
    onPointerUp: (event: PointerEvent<HTMLElement>) => {
      const s = session.current;
      if (s?.pointerId !== event.pointerId) return;
      s.lastY = event.clientY;
      end(true);
    },
    onPointerCancel: () => {
      end(false);
    },
    onLostPointerCapture: () => {
      // After `onPointerUp` this finds no session and does nothing.
      end(false);
    },
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
      if (disabled || session.current !== null) return;
      const to =
        event.key === 'ArrowUp' ? index - 1 : event.key === 'ArrowDown' ? index + 1 : null;
      if (to === null) return;
      event.preventDefault();
      if (to < 0 || to >= keys.length) return;
      focusAfterMove.current = to;
      onMoveRef.current(index, to, 'keyboard');
    },
  });

  return { listProps, itemProps, handleProps, dragging };
}
