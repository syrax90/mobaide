import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '../../../lib/queryKeys';
import { GitChangesList } from './GitChangesList';
import { GitDiffPane } from './GitDiffPane';
import { DIFF_CONTEXT_DEFAULT, sectionsFor, type ChangesRow } from './sessionGitDisplay';
import { useSessionGitStatus } from './useSessionGit';

interface GitChangesPanelProps {
  agentId: string;
  sessionId: string;
  onClose: () => void;
}

/**
 * The in-session Changes viewer: a slide-over on the top layer with the file
 * list beside the diff (stacked below 900 px).
 *
 * `popover="manual"`, not a `<dialog>`: the terminal stays live underneath —
 * the point of a viewer next to the PTY that produces the changes — and a
 * non-modal `dialog.show()` is not on the top layer, where xterm's canvases
 * would hit-test above it. Manual, not auto, so a click into the terminal
 * does not dismiss it; Escape and the close button do, and the caller hands
 * focus back to the menu that opened it. Mounted only while open, the way
 * `Modal` is, so the poll below simply stops with it.
 */
export function GitChangesPanel({ agentId, sessionId, onClose }: GitChangesPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<ChangesRow | null>(null);
  const [context, setContext] = useState(DIFF_CONTEXT_DEFAULT);
  const [view, setView] = useState<'list' | 'diff'>('list');
  const status = useSessionGitStatus(agentId, sessionId, true);

  useLayoutEffect(() => {
    const panel = panelRef.current;
    panel?.showPopover();
    closeRef.current?.focus();
    return () => {
      panel?.hidePopover();
    };
  }, []);

  const invalidateDiffs = (): void => {
    void queryClient.invalidateQueries({
      queryKey: queryKeys.sessionGitDiffs(agentId, sessionId),
    });
  };

  // A new etag means the working tree moved, so an open diff is re-read too;
  // the list itself is already the new body.
  const etag = status.data?.etag;
  const lastEtag = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (etag === undefined || etag === lastEtag.current) return;
    lastEtag.current = etag;
    void queryClient.invalidateQueries({
      queryKey: queryKeys.sessionGitDiffs(agentId, sessionId),
    });
  }, [etag, queryClient, agentId, sessionId]);

  // A refresh can move the selected file to another section — `git add` typed
  // into the PTY beside the panel — or drop it. Follow it by path so the
  // highlight and the diff's scope track the list, and let go once it is gone.
  const body = status.data;
  useEffect(() => {
    if (body === undefined || selection === null) return;
    const rows = sectionsFor(body).flatMap((section) => section.rows);
    const same = rows.find((row) => row.key === selection.key);
    if (same !== undefined) {
      if (same.entry !== selection.entry) setSelection(same);
      return;
    }
    const { path, group } = selection.entry;
    const next =
      rows.find((row) => row.entry.path === path && row.entry.group === group) ??
      rows.find((row) => row.entry.path === path) ??
      null;
    setSelection(next);
    if (next === null) setView('list');
  }, [body, selection]);

  const refresh = (): void => {
    void status.refetch();
    invalidateDiffs();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    onClose();
  };

  return (
    <div
      ref={panelRef}
      popover="manual"
      role="dialog"
      aria-modal="false"
      aria-label="Git changes"
      className="git-panel"
      onKeyDown={onKeyDown}
    >
      <div className="git-panel-head">
        <h2>Changes</h2>
        <button
          type="button"
          className="button"
          onClick={refresh}
          aria-busy={status.isFetching}
          title="Re-read the working tree"
        >
          Refresh
        </button>
        <button
          ref={closeRef}
          type="button"
          className="button button-icon"
          aria-label="Close"
          title="Close"
          onClick={onClose}
        >
          ✕
        </button>
      </div>
      <div className="git-panel-body" data-view={view}>
        <div className="git-panel-list">
          <GitChangesList
            status={status.data}
            error={status.error}
            selectedKey={selection?.key ?? null}
            onSelect={(row) => {
              setSelection(row);
              setView('diff');
            }}
            onRetry={() => {
              void status.refetch();
            }}
          />
        </div>
        <div className="git-panel-diff">
          <GitDiffPane
            agentId={agentId}
            sessionId={sessionId}
            selection={selection}
            context={context}
            onContextChange={setContext}
            onBack={() => {
              setView('list');
            }}
          />
        </div>
      </div>
    </div>
  );
}
