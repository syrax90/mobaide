const TAIL_MAX = 24;

/** Where the always-visible tail starts: the last path segment when it is short. */
function tailStart(text: string): number {
  const body = text.endsWith('/') || text.endsWith('\\') ? text.slice(0, -1) : text;
  const cut = Math.max(body.lastIndexOf('/'), body.lastIndexOf('\\'));
  if (cut !== -1 && text.length - cut <= TAIL_MAX) return cut;
  return Math.max(0, text.length - Math.min(16, text.length));
}

/**
 * Elides the middle of a long path, keeping its end readable. The head
 * shrinks with an ellipsis and the tail never does, so it adapts to any width.
 */
export function MiddleEllipsis({ text, className }: { text: string; className?: string }) {
  const start = tailStart(text);
  return (
    <span
      className={className === undefined ? 'mid-ellipsis' : `mid-ellipsis ${className}`}
      title={text}
    >
      <span className="mid-ellipsis-head">{text.slice(0, start)}</span>
      <span className="mid-ellipsis-tail">{text.slice(start)}</span>
    </span>
  );
}
