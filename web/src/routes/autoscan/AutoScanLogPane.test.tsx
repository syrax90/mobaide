import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { LogLine } from '../../autoscan/autoscanModel';
import { AutoScanLogPane } from './AutoScanLogPane';

interface Geometry {
  scrollHeight: number;
  clientHeight: number;
  scrollTop: number;
}

/**
 * jsdom has no layout: the scroll metrics are all 0 and `scrollTop` ignores
 * writes, so the pane can neither pin nor release without a stand-in. The
 * returned object is the viewport's geometry — writable from the test, and
 * written through by the component.
 */
function stubViewport(node: HTMLElement): Geometry {
  const geometry: Geometry = { scrollHeight: 400, clientHeight: 100, scrollTop: 0 };
  for (const key of ['scrollHeight', 'clientHeight', 'scrollTop'] as const) {
    Object.defineProperty(node, key, {
      configurable: true,
      get: () => geometry[key],
      set: (value: number) => {
        geometry[key] = value;
      },
    });
  }
  return geometry;
}

function lines(count: number): LogLine[] {
  return Array.from({ length: count }, (_, index) => ({
    id: index,
    text: `line ${String(index)}`,
    ansi: false,
  }));
}

function viewport(): HTMLElement {
  return screen.getByRole('log');
}

/** Render, then give the mounted viewport a geometry and scroll it to `scrollTop`. */
function renderPane(count: number, scrollTop = 400 - 100) {
  const view = render(<AutoScanLogPane lines={lines(count)} />);
  const geometry = stubViewport(viewport());
  geometry.scrollTop = scrollTop;
  return { geometry, append: (next: number) => view.rerender(<AutoScanLogPane lines={lines(next)} />) };
}

afterEach(() => {
  cleanup();
});

describe('AutoScanLogPane', () => {
  it('keeps the newest line in view while the reader is at the bottom', () => {
    const { geometry, append } = renderPane(3);

    append(4);

    expect(geometry.scrollTop).toBe(400);
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('releases the pin only once the reader is more than a line clear of the bottom', () => {
    const { geometry } = renderPane(3);

    geometry.scrollTop = 280;
    fireEvent.scroll(viewport());
    expect(screen.queryByRole('button')).toBeNull();

    geometry.scrollTop = 260;
    fireEvent.scroll(viewport());

    expect(screen.getByRole('button')).toHaveTextContent('Jump to latest');
  });

  it('stops following and counts only what arrived after the release', () => {
    const { geometry, append } = renderPane(3);
    geometry.scrollTop = 0;
    fireEvent.scroll(viewport());

    append(5);

    expect(geometry.scrollTop).toBe(0);
    expect(screen.getByRole('button')).toHaveTextContent('2 new');
  });

  it('re-pins and drops the badge on jump to latest', () => {
    const { geometry, append } = renderPane(3);
    geometry.scrollTop = 0;
    fireEvent.scroll(viewport());
    append(5);

    fireEvent.click(screen.getByRole('button'));

    expect(geometry.scrollTop).toBe(400);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
