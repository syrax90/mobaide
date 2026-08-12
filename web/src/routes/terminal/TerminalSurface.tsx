import { useLayoutEffect, useRef } from 'react';
import type { TerminalSessionModel } from '../../terminal/sessionModel';

interface TerminalSurfaceProps {
  model: TerminalSessionModel;
  /** Referenced by the scrollbar's `aria-controls`. */
  id: string;
}

/**
 * The one place a `Terminal` is in the DOM.
 *
 * The model owns its host element and keeps it across mounts; this component
 * only adopts it. `open()` cannot run under `display: none` and `FitAddon`
 * mis-measures there, so a hidden terminal is an unmounted one — never a
 * hidden div — and the emulator survives because it lives in the model, not
 * here.
 *
 * `useLayoutEffect` rather than `useEffect`: the model measures the container
 * on attach, and a paint in between would size the grid off a stale layout.
 */
export function TerminalSurface({ model, id }: TerminalSurfaceProps) {
  const container = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const node = container.current;
    if (node === null) return;
    model.attach(node);
    model.focus();
    return () => {
      model.detach();
    };
  }, [model]);

  return <div className="terminal-surface" id={id} ref={container} />;
}
