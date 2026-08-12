import { useCallback, useRef, type CSSProperties, type PointerEvent } from 'react';
import type { KeyboardEvent, WheelEvent } from 'react';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { offsetFromDrag } from '../../terminal/terminalScrollbar';

/**
 * A pointerdown this control acts on: the primary pointer's primary button.
 *
 * Without it a right-click would open the context menu *and* start a drag, a
 * middle-click would page, and a second finger landing during a drag would
 * fight the first for the thumb.
 */
function isPrimaryPress(event: PointerEvent<HTMLDivElement>): boolean {
  return event.isPrimary && event.button === 0;
}

interface Drag {
  pointerId: number;
  trackTop: number;
  trackHeight: number;
  thumbHeight: number;
  grabOffset: number;
}

interface TerminalScrollbarProps {
  model: TerminalSessionModel;
  /** The `.terminal-surface` this scrolls; `role="scrollbar"` requires it. */
  surfaceId: string;
}

function thumbStyle(size: number, offset: number): CSSProperties {
  // Custom properties rather than resolved `height`/`top`: the CSS folds a
  // minimum thumb height into both, so the two have to be computed together in
  // the stylesheet or a clamped thumb overruns the bottom of the track.
  return {
    '--thumb-size': `${String(size * 100)}%`,
    '--thumb-offset': String(offset),
  } as CSSProperties;
}

/**
 * The scrollback slider.
 *
 * It renders from two fractions and takes no measurement, so it needs no
 * `ResizeObserver` and survives jsdom, which computes no layout. Only a drag
 * reads real pixels, and it reads them once at `pointerdown`.
 *
 * The track sits in the gutter `FitAddon` already reserves (see
 * `terminalScrollbar.ts`), so it costs no columns. It is absolutely positioned
 * inside `.terminal-stage` for the same reason every other terminal overlay is:
 * a flex sibling would change the row count, and xterm clears the selection on
 * any row-count change.
 */
export function TerminalScrollbar({ model, surfaceId }: TerminalScrollbarProps) {
  const { scrollable, size, offset } = model.scrollbar();
  const trackRef = useRef<HTMLDivElement>(null);
  const thumbRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<Drag | null>(null);

  const startDrag = useCallback((event: PointerEvent<HTMLDivElement>) => {
    const track = trackRef.current;
    // Deliberately before `stopPropagation`, so a non-primary press falls
    // through to the track handler — which refuses it on the same test.
    if (track === null || !isPrimaryPress(event)) return;
    const thumbRect = event.currentTarget.getBoundingClientRect();
    const trackRect = track.getBoundingClientRect();
    dragRef.current = {
      pointerId: event.pointerId,
      trackTop: trackRect.top,
      trackHeight: trackRect.height,
      thumbHeight: thumbRect.height,
      grabOffset: event.clientY - thumbRect.top,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    // Claims the gesture before the track's paging handler sees it, and stops
    // the browser starting a text selection under the finger.
    event.stopPropagation();
    event.preventDefault();
  }, []);

  const onDrag = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (drag === null || drag.pointerId !== event.pointerId) return;
      model.scrollToOffset(
        offsetFromDrag({
          pointerY: event.clientY,
          trackTop: drag.trackTop,
          trackHeight: drag.trackHeight,
          thumbHeight: drag.thumbHeight,
          grabOffset: drag.grabOffset,
        }),
      );
    },
    [model],
  );

  const endDrag = useCallback(() => {
    dragRef.current = null;
  }, []);

  const pageToPointer = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      const thumb = thumbRef.current;
      if (thumb === null || !isPrimaryPress(event)) return;
      // Paging must not pull focus off the grid, or the next keystroke is
      // delivered to this div and never reaches the PTY — the terminal looks
      // dead until the user clicks the grid again. Native scrollbars do not
      // take focus on a press either, and `Tab` still reaches this one.
      event.preventDefault();
      const rect = thumb.getBoundingClientRect();
      if (event.clientY < rect.top) model.scrollByPages(-1);
      else if (event.clientY > rect.bottom) model.scrollByPages(1);
    },
    [model],
  );

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      switch (event.key) {
        case 'ArrowUp':
          model.scrollByLines(-1);
          break;
        case 'ArrowDown':
          model.scrollByLines(1);
          break;
        case 'PageUp':
          model.scrollByPages(-1);
          break;
        case 'PageDown':
          model.scrollByPages(1);
          break;
        case 'Home':
          model.scrollToTop();
          break;
        case 'End':
          model.jumpToBottom();
          break;
        default:
          return;
      }
      event.preventDefault();
    },
    [model],
  );

  // The track covers the strip the viewport's own scrollbar used to occupy, so
  // without this a wheel over the last few pixels would land on a div and do
  // nothing. React registers `wheel` passively, so there is no `preventDefault`
  // to make here — the app shell does not scroll anyway.
  //
  // The three delta modes are kept apart rather than collapsed to a notch
  // count: a precision touchpad reports pixels, in a stream, and one notch per
  // event would run the scrollback many times faster here than over the grid.
  const onWheel = useCallback(
    (event: WheelEvent<HTMLDivElement>) => {
      if (event.deltaY === 0) return;
      if (event.deltaMode === 2) {
        model.scrollByPages(Math.sign(event.deltaY));
        return;
      }
      if (event.deltaMode === 1) {
        model.scrollByLines(Math.round(event.deltaY));
        return;
      }
      model.scrollByPixels(event.deltaY);
    },
    [model],
  );

  // Nothing above the viewport — the alternate screen, or a session that has
  // not filled one page yet. The track stays so the gutter never looks empty,
  // but there is no control to expose.
  if (!scrollable) {
    return <div className="terminal-scrollbar" aria-hidden="true" />;
  }

  return (
    <div
      ref={trackRef}
      className="terminal-scrollbar"
      role="scrollbar"
      tabIndex={0}
      aria-label="Terminal scrollback"
      aria-orientation="vertical"
      aria-controls={surfaceId}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(offset * 100)}
      onPointerDown={pageToPointer}
      onKeyDown={onKeyDown}
      onWheel={onWheel}
    >
      <div
        ref={thumbRef}
        className="terminal-scrollbar-thumb"
        style={thumbStyle(size, offset)}
        onPointerDown={startDrag}
        onPointerMove={onDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={endDrag}
      />
    </div>
  );
}
