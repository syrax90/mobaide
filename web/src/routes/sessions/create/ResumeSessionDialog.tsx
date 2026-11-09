import { useId, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { listChatSessions } from '../../../api/chatRepository';
import { formatRelativeTime } from '../../../chat/chatFormat';
import { ErrorState } from '../../../components/ErrorState';
import { Modal } from '../../../components/Modal';
import { queryKeys } from '../../../lib/queryKeys';
import type { ChatStoredSession } from '../../../protocol/dto/chatSessions';
import type { ChatAgentKind } from './buildCreateRequest';

type Scope = 'folder' | 'all';

interface ResumeSessionDialogProps {
  agentId: string;
  kind: ChatAgentKind;
  /** Sessions stay in sync with the agent's own apps on the PC (`capabilities.chat.<kind>.live`). */
  live: boolean;
  /** The selected project's or typed folder's path; `null` offers only All folders. */
  folder: string | null;
  /** The stored session being opened right now. */
  opening: string | null;
  error: string | null;
  onPick: (session: ChatStoredSession) => void;
  onClose: () => void;
}

/**
 * "Continue a session": the agent's sessions stored on the PC — also the ones
 * started outside Mobaide — newest first, a page at a time. Picking one
 * continues it in a new chat session, or opens the session already showing it.
 */
export function ResumeSessionDialog({
  agentId,
  kind,
  live,
  folder,
  opening,
  error,
  onPick,
  onClose,
}: ResumeSessionDialogProps) {
  const titleId = useId();
  const scopeName = useId();
  const [scope, setScope] = useState<Scope>(folder === null ? 'all' : 'folder');
  const [now] = useState(() => Date.now());
  const cwd = scope === 'folder' ? folder : null;

  const sessions = useInfiniteQuery({
    queryKey: queryKeys.chatStoredSessions(agentId, kind, cwd),
    queryFn: ({ pageParam, signal }) =>
      listChatSessions(agentId, kind, { cwd: cwd ?? undefined, cursor: pageParam, signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? null,
    staleTime: 0,
    gcTime: 60_000,
    refetchOnWindowFocus: false,
    meta: { silent: true },
  });
  const rows = sessions.data?.pages.flatMap((page) => page.sessions) ?? [];
  const busy = opening !== null;

  return (
    <Modal labelledBy={titleId} onClose={onClose} dismissible={!busy} className="resume-dialog">
      <h2 id={titleId}>Continue a session</h2>
      <p className="page-note">
        {live
          ? 'A session that is open on your PC stays in sync: both sides see every message.'
          : 'If the session is still open in a terminal on your PC, close it there first.'}
      </p>

      <fieldset className="resume-scope">
        <legend className="visually-hidden">Which folders</legend>
        {(['folder', 'all'] as const).map((value) => (
          <label key={value} className="resume-scope-option">
            <input
              type="radio"
              className="visually-hidden"
              name={scopeName}
              value={value}
              checked={scope === value}
              disabled={value === 'folder' && folder === null}
              onChange={() => {
                setScope(value);
              }}
            />
            {value === 'folder' ? 'This folder' : 'All folders'}
          </label>
        ))}
      </fieldset>

      <div className="resume-body">
        {sessions.isPending ? (
          <p className="page-note state-loading" role="status">
            Looking for sessions…
          </p>
        ) : rows.length === 0 && sessions.isError ? (
          <ErrorState
            message={sessions.error.message}
            onRetry={() => void sessions.refetch()}
          />
        ) : rows.length === 0 ? (
          <p className="page-note">
            {scope === 'folder' ? 'No earlier sessions in this folder.' : 'No earlier sessions.'}
          </p>
        ) : (
          <ul className="resume-list">
            {rows.map((row) => {
              const when = row.updated_at === undefined ? null : formatRelativeTime(row.updated_at, now);
              return (
                <li key={row.session_id}>
                  <button
                    type="button"
                    className="resume-row"
                    disabled={busy}
                    aria-busy={opening === row.session_id}
                    onClick={() => {
                      onPick(row);
                    }}
                  >
                    <span className="resume-row-main">
                      <span className="resume-row-title">
                        {row.title === undefined || row.title.trim() === ''
                          ? 'Untitled session'
                          : row.title}
                      </span>
                      {scope === 'all' && <span className="resume-row-cwd">{row.cwd}</span>}
                    </span>
                    {row.open_session_id !== undefined && <span className="chat-badge">Open</span>}
                    {when !== null && <span className="resume-row-time">{when}</span>}
                    {opening === row.session_id && <span className="chat-spinner" aria-hidden="true" />}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {sessions.hasNextPage && (
          <button
            type="button"
            className="button"
            disabled={sessions.isFetchingNextPage}
            onClick={() => void sessions.fetchNextPage()}
          >
            {sessions.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </button>
        )}
        {sessions.isFetchNextPageError && (
          <p className="field-error" role="alert">
            {sessions.error.message}
          </p>
        )}
      </div>

      {error !== null && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      <div className="button-row">
        <button type="button" className="button" disabled={busy} onClick={onClose}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}
