/**
 * WebGL renderer with a bounded context-loss ladder.
 *
 * xterm 6 removed the canvas renderer, so the choice is WebGL or DOM. The
 * addon's own README calls the dispose-only sample "suboptimal" and it is: a
 * context loss is usually transient (a GPU reset, a driver update, the tab
 * getting backgrounded on a machine under memory pressure), and staying on the
 * DOM renderer for the rest of the tab's life costs real throughput on a 4 MiB
 * replay.
 *
 * Ladder: try WebGL once; on loss dispose it — which drops xterm back to the DOM
 * renderer with the buffer intact — then re-arm once after a delay. A second
 * loss means the environment genuinely cannot keep a context, so stay on DOM.
 */
import type { Terminal } from '@xterm/xterm';
import { WebglAddon } from '@xterm/addon-webgl';
import { realScheduler, type Scheduler } from '../realtime/webSocketChannel';

export type RendererKind = 'webgl' | 'dom';

/** How long to wait before re-arming WebGL after a context loss. */
const RE_ARM_DELAY_MS = 1000;

/** Total WebGL activations allowed: the initial one plus a single retry. */
const MAX_WEBGL_ATTEMPTS = 2;

export interface RendererHandle {
  current: () => RendererKind;
  dispose: () => void;
}

export interface RendererOptions {
  scheduler?: Scheduler;
  /** Fired whenever the active renderer changes, so the UI can report it. */
  onChange?: (kind: RendererKind) => void;
}

export function attachRenderer(
  terminal: Terminal,
  options: RendererOptions = {},
): RendererHandle {
  const scheduler = options.scheduler ?? realScheduler;
  let addon: WebglAddon | null = null;
  let attempts = 0;
  let cancelReArm: (() => void) | null = null;
  let disposed = false;

  const announce = (): void => {
    options.onChange?.(addon === null ? 'dom' : 'webgl');
  };

  const activate = (): void => {
    if (disposed || attempts >= MAX_WEBGL_ATTEMPTS) return;
    attempts += 1;
    let next: WebglAddon;
    try {
      next = new WebglAddon();
      terminal.loadAddon(next);
    } catch {
      // No WebGL2 at all (a locked-down browser, a headless run, software
      // rendering disabled). The DOM renderer is already live; nothing to undo.
      announce();
      return;
    }
    addon = next;
    announce();
    next.onContextLoss(() => {
      // Disposing is what hands rendering back to the DOM renderer. The buffer
      // is owned by the core, not the renderer, so nothing on screen is lost.
      addon = null;
      next.dispose();
      announce();
      cancelReArm?.();
      cancelReArm = scheduler.schedule(() => {
        cancelReArm = null;
        activate();
      }, RE_ARM_DELAY_MS);
    });
  };

  activate();

  return {
    current: () => (addon === null ? 'dom' : 'webgl'),
    dispose: () => {
      disposed = true;
      cancelReArm?.();
      cancelReArm = null;
      addon?.dispose();
      addon = null;
    },
  };
}
