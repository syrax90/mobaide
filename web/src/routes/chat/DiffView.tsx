import { memo, useMemo, useState } from 'react';
import { splitPath } from '../../chat/chatFormat';
import type { DiffPart } from '../../chat/chatWire';
import { MiddleEllipsis } from './MiddleEllipsis';

const PREVIEW_LINES = 30;

function lineClass(line: string): string {
  if (line.startsWith('@@')) return 'chat-diff-line chat-diff-hunk';
  if (line.startsWith('+')) return 'chat-diff-line chat-diff-add';
  if (line.startsWith('-')) return 'chat-diff-line chat-diff-del';
  return 'chat-diff-line';
}

/** One file's change: a header row and the unified body, scrolled as one block. */
export const DiffView = memo(function DiffView({ part }: { part: DiffPart }) {
  const [expanded, setExpanded] = useState(false);
  const lines = useMemo(() => (part.diff === '' ? [] : part.diff.split('\n')), [part.diff]);
  const { dir, name } = splitPath(part.path);
  const shown = expanded ? lines : lines.slice(0, PREVIEW_LINES);
  return (
    <div className="chat-diff">
      <div className="chat-diff-head">
        <span className="chat-diff-name">{name}</span>
        {dir !== '' && <MiddleEllipsis className="chat-diff-dir" text={dir} />}
        {part.new_file && <span className="chat-badge">New file</span>}
        <span className="chat-diff-stat">
          <span className="chat-stat-add">+{part.added}</span>{' '}
          <span className="chat-stat-del">−{part.removed}</span>
        </span>
      </div>
      {lines.length === 0 ? (
        <p className="chat-diff-note">
          {part.truncated ? 'Too large to show a diff' : 'No changes to show'}
        </p>
      ) : (
        <>
          <pre className="chat-diff-body" tabIndex={0}>
            <code>
              {shown.map((line, index) => (
                <span key={index} className={lineClass(line)}>
                  {line === '' ? ' ' : line}
                  {'\n'}
                </span>
              ))}
            </code>
          </pre>
          {lines.length > PREVIEW_LINES && (
            <button
              type="button"
              className="chat-link-button"
              aria-expanded={expanded}
              onClick={() => {
                setExpanded((value) => !value);
              }}
            >
              {expanded ? 'Show less' : `Show all ${String(lines.length)} lines`}
            </button>
          )}
          {part.truncated && <p className="chat-diff-note">Diff truncated</p>}
        </>
      )}
    </div>
  );
});
