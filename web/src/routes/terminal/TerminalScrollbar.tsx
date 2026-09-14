import { useCallback, useRef, type CSSProperties, type PointerEvent } from 'react';
import type { KeyboardEvent, WheelEvent } from 'react';
import { isPrimaryPress } from '../../lib/pointer';
import type { TerminalSessionModel } from '../../terminal/sessionModel';
import { offsetFromDrag } from '../../terminal/terminalScrollbar';

/** Seek drag: absolute, so it measures the track once and maps into it. */
interface SeekDrag {
  pointerId: number;
  trackTop: number;
  trackHeight: number;
  thumbHeight: number;
  grabOffset: number;
}

/** Grip drag: relative, so only the previous position matters. */
interface GripDrag {
  pointerId: number;
  lastY: number;
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
 * The scroll control, in whichever of its two forms the session can use.
 *
 * With scrollback it is a scrollbar: the thumb shows where the viewport sits
 * and dragging it seeks. Without — the alternate screen, or any program holding
 * mouse tracking — there is no position to show, because the program owns its
 * own viewport. It becomes a grip parked mid-track that reports travel instead,
 * which is the same trade both mobile clients make (iOS `TerminalWheelScroll`,
 * Android `doScroll`); it just springs back rather than following a finger on
 * the grid.
 *
 * It renders from fractions and takes no measurement, so it needs no
 * `ResizeObserver` and survives jsdom, which computes no layout. Only a drag
 * reads real pixels.
 *
 * The track sits in the gutter `FitAddon` already reserves (see
 * `terminalScrollbar.ts`), so it costs no columns. It is absolutely positioned
 * inside `.terminal-stage` for the same reason every other terminal overlay is:
 * a flex sibling would change the row count, and xterm clears the selection on
 * any row-count change.
 */
export function TerminalScrollbar({ model, surfaceId }: TerminalScrollbarProps) {
  const { mode, size, offset } = model.scrollbar();
  const trackRef = useRef<HTMLDivElement>(null);
  const thumbRef = useRef<HTMLDivElement>(null);
  const seekRef = useRef<SeekDrag | null>(null);
  const gripRef = useRef<GripDrag | null>(null);

  const startSeek = useCallback((event: PointerEvent<HTMLElement>) => {
    const track = trackRef.current;
    // Deliberately before `stopPropagation`, so a non-primary press falls
    // through to the track handler — which refuses it on the same test.
    if (track === null || !isPrimaryPress(event)) return;
    const thumbRect = event.currentTarget.getBoundingClientRect();
    const trackRect = track.getBoundingClientRect();
    seekRef.current = {
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

  const onSeek = useCallback(
    (event: PointerEvent<HTMLElement>) => {
      const drag = seekRef.current;
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

  const startGrip = useCallback((event: PointerEvent<HTMLElement>) => {
    if (!isPrimaryPress(event)) return;
    gripRef.current = { pointerId: event.pointerId, lastY: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.stopPropagation();
    event.preventDefault();
  }, []);

  // Travel since the last move, not since the press: the grip is a rate of
  // travel rather than a position, so an absolute origin would keep re-sending
  // the whole drag on every move.
  const onGrip = useCallback(
    (event: PointerEvent<HTMLElement>) => {
      const drag = gripRef.current;
      if (drag === null || drag.pointerId !== event.pointerId) return;
      const travel = event.clientY - drag.lastY;
      drag.lastY = event.clientY;
      model.relativeScrollByPixels(travel);
    },
    [model],
  );

  // Keyed on the pointer that started the drag, exactly like the move handlers.
  // Capture is per-pointer, so a second finger landing on the thumb is hit-
  // tested here normally and its `pointerup` would otherwise end a drag the
  // first finger is still holding.
  const endDrag = useCallback((event: PointerEvent<HTMLElement>) => {
    if (seekRef.current?.pointerId === event.pointerId) seekRef.current = null;
    if (gripRef.current?.pointerId === event.pointerId) gripRef.current = null;
  }, []);

  const pageToPointer = useCallback(
    (event: PointerEvent<HTMLElement>) => {
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
    (event: KeyboardEvent<HTMLElement>) => {
      const relative = mode === 'relative';
      switch (event.key) {
        case 'ArrowUp':
          if (relative) model.relativeScroll(-1);
          else model.scrollByLines(-1);
          break;
        case 'ArrowDown':
          if (relative) model.relativeScroll(1);
          else model.scrollByLines(1);
          break;
        case 'PageUp':
          if (relative) model.relativeScrollPage(-1);
          else model.scrollByPages(-1);
          break;
        case 'PageDown':
          if (relative) model.relativeScrollPage(1);
          else model.scrollByPages(1);
          break;
        case 'Home':
          if (relative) return;
          model.scrollToTop();
          break;
        case 'End':
          if (relative) return;
          model.jumpToBottom();
          break;
        default:
          return;
      }
      event.preventDefault();
    },
    [model, mode],
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
    (event: WheelEvent<HTMLElement>) => {
      if (event.deltaY === 0) return;
      const relative = mode === 'relative';
      if (event.deltaMode === 2) {
        if (relative) model.relativeScrollPage(Math.sign(event.deltaY));
        else model.scrollByPages(Math.sign(event.deltaY));
        return;
      }
      if (event.deltaMode === 1) {
        const lines = Math.round(event.deltaY);
        if (relative) model.relativeScroll(lines);
        else model.scrollByLines(lines);
        return;
      }
      if (relative) model.relativeScrollByPixels(event.deltaY);
      else model.scrollByPixels(event.deltaY);
    },
    [model, mode],
  );

  // Nothing to scroll and nobody to ask: a primary buffer that has not filled
  // one screen. The track stays so the gutter never looks empty, but with no
  // control to expose it drops out of the hit path entirely — the strip lies
  // over the terminal, and events landing here have to reach xterm.
  if (mode === 'idle') {
    return (
      <div className="terminal-scrollbar terminal-scrollbar-idle" aria-hidden="true" />
    );
  }

  if (mode === 'relative') {
    return (
      <div
        ref={trackRef}
        className="terminal-scrollbar"
        role="group"
        aria-label="Scroll terminal"
        onWheel={onWheel}
      >
        {/* A button, not a `scrollbar`: there is no position to report, so
            `aria-valuenow` would be a fiction. Focusable, so the arrow and page
            keys above are reachable without a pointer. */}
        <button
          type="button"
          className="terminal-scrollbar-grip"
          aria-label="Drag to scroll the terminal"
          aria-controls={surfaceId}
          onKeyDown={onKeyDown}
          onPointerDown={startGrip}
          onPointerMove={onGrip}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onLostPointerCapture={endDrag}
        />
      </div>
    );
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
        onPointerDown={startSeek}
        onPointerMove={onSeek}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={endDrag}
      />
    </div>
  );
}
