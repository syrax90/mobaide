import { memo, useCallback, useLayoutEffect, useRef, useState } from 'react';
import type { LogLine } from '../../autoscan/autoscanModel';
import { AnsiLine } from './AnsiLine';

interface AutoScanLogPaneProps {
  lines: readonly LogLine[];
}

/**
 * How close to the bottom still counts as "following". One line of slack, so a
 * fractional scroll height or a sub-pixel layout rounding cannot silently
 * release the pin.
 */
const FOLLOW_EPSILON_PX = 24;

/**
 * The live log.
 *
 * **Not virtualised, deliberately.** The ring caps at 2000 lines, rows are
 * memoised on an id that is never recycled, and `content-visibility: auto` lets
 * the browser skip layout and paint for anything off screen — which is the
 * expensive part. What that buys over a windowing library is the two things a
 * log reader actually needs: selecting and copying the whole buffer, and the
 * browser's own scroll anchoring keeping the view still when the ring trims its
 * oldest line off the top.
 *
 * Follow mode is derived from the viewport rather than latched, the same shape
 * the terminal screen uses: whenever the user is at the bottom we pin, and any
 * scroll away releases it until they come back.
 *
 * Memoised because `logLines` is referentially stable between reducer writes
 * (`fromDraft` hands back the same state object when a batch changed nothing),
 * so an unrelated re-render of the screen — the countdown ticking, a mutation
 * settling — costs nothing here.
 */
export const AutoScanLogPane = memo(function AutoScanLogPane({
  lines,
}: AutoScanLogPaneProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  // Line ids are monotonic, so the id at the moment the pin was released is an
  // exact watermark — no counter to keep in sync with the ring's trimming.
  const [releasedAt, setReleasedAt] = useState<number | null>(null);

  // Read by the scroll handler, which must not be rebuilt on every appended
  // line just to know where the buffer ends.
  const lastIdRef = useRef<number | null>(null);
  lastIdRef.current = lines.at(-1)?.id ?? null;

  const handleScroll = useCallback(() => {
    const node = viewportRef.current;
    if (node === null) return;
    const bottom =
      node.scrollHeight - node.scrollTop - node.clientHeight <= FOLLOW_EPSILON_PX;
    setFollowing(bottom);
    setReleasedAt((current) => (bottom ? null : (current ?? lastIdRef.current)));
  }, []);

  // Layout effect, not effect: scrolling after paint shows one frame of the old
  // offset, which reads as a flicker on every appended line.
  useLayoutEffect(() => {
    const node = viewportRef.current;
    if (node === null || !following) return;
    node.scrollTop = node.scrollHeight;
  }, [lines, following]);

  // Arithmetic, not a scan: ids are monotonic and never recycled, so the gap to
  // the watermark *is* the count — and the ring caps how many of those lines are
  // still resident.
  const unread =
    releasedAt === null
      ? 0
      : Math.min((lastIdRef.current ?? releasedAt) - releasedAt, lines.length);

  const jumpToBottom = () => {
    const node = viewportRef.current;
    if (node === null) return;
    setFollowing(true);
    setReleasedAt(null);
    node.scrollTop = node.scrollHeight;
  };

  return (
    <div className="autoscan-log">
      <div
        className="autoscan-log-viewport"
        ref={viewportRef}
        onScroll={handleScroll}
        role="log"
        aria-label="Auto Scan output"
        aria-live="off"
        tabIndex={0}
      >
        {lines.length === 0 ? (
          <p className="page-note">Waiting for output…</p>
        ) : (
          lines.map((line) => <LogRow key={line.id} line={line} />)
        )}
      </div>

      {!following && (
        <button type="button" className="button autoscan-log-jump" onClick={jumpToBottom}>
          {unread === 0 ? 'Jump to latest' : `${formatUnread(unread)} new`}
        </button>
      )}
    </div>
  );
});

/** Saturates like the terminal's unread badge, so the button never resizes. */
function formatUnread(count: number): string {
  return count > 999 ? '999+' : String(count);
}

const LogRow = memo(function LogRow({ line }: { line: LogLine }) {
  return (
    <div className="autoscan-log-line">
      <AnsiLine text={line.text} ansi={line.ansi} />
    </div>
  );
});
