import type { KeyboardEvent, PointerEvent } from 'react';

/** Shown under a reorderable list's title; the same copy on Android. */
export const REORDER_HINT = 'Drag a handle to reorder — the order syncs to all your devices.';

interface DragHandleProps {
  /** The row's name, for the accessible label. */
  label: string;
  disabled: boolean;
  onPointerDown: (event: PointerEvent<HTMLElement>) => void;
  onPointerMove: (event: PointerEvent<HTMLElement>) => void;
  onPointerUp: (event: PointerEvent<HTMLElement>) => void;
  onPointerCancel: () => void;
  onLostPointerCapture: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
  'data-sortable-handle': boolean;
}

/**
 * The grip a wizard row is dragged by (`useDragReorder`). A real button, so it
 * takes focus and the arrow keys; the hint names both ways to use it.
 */
export function DragHandle({ label, ...handle }: DragHandleProps) {
  return (
    <button
      type="button"
      className="drag-handle"
      aria-label={`Reorder ${label}`}
      title="Drag to reorder, or focus and use the arrow keys"
      {...handle}
    >
      <span aria-hidden="true">⠿</span>
    </button>
  );
}
