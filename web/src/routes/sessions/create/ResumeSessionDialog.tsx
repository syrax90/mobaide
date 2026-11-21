import { useId, useRef, useState } from 'react';
import { useInfiniteQuery, useMutation } from '@tanstack/react-query';
import { deleteChatSession, listChatSessions } from '../../../api/chatRepository';
import { formatRelativeTime } from '../../../chat/chatFormat';
import { ErrorState } from '../../../components/ErrorState';
import { Modal } from '../../../components/Modal';
import { queryKeys } from '../../../lib/queryKeys';
import type { ChatStoredSession } from '../../../protocol/dto/chatSessions';
import type { ChatAgentKind } from './buildCreateRequest';

type Scope = 'folder' | 'all';

/** What deleting does on the PC; the agent decides between a soft and a hard delete. */
export function deleteNote(kind: ChatAgentKind): string {
  return kind === 'codex'
    ? 'Codex moves this session to its archive on your PC. It no longer shows here or in the Codex terminal app.'
    : 'This session is removed from your PC, also from the history of the agent’s terminal app. You cannot undo this.';
}

/** Delete is offered only when the agent supports it and nothing has the session open. */
export function canDeleteRow(canDelete: boolean, row: ChatStoredSession): boolean {
  return canDelete && row.open_session_id === undefined && row.open_elsewhere !== true;
}

function rowTitle(row: ChatStoredSession): string {
  return row.title === undefined || row.title.trim() === '' ? 'Untitled session' : row.title;
}

interface ResumeSessionDialogProps {
  agentId: string;
  kind: ChatAgentKind;
  /** Sessions stay in sync with the agent's own apps on the PC (`capabilities.chat.<kind>.live`). */
  live: boolean;
  /** The selected workspace's or typed folder's path; `null` offers only All folders. */
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
  const confirmTitleId = useId();
  const keepRef = useRef<HTMLButtonElement>(null);
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
  // Rows deleted here leave the list locally: a reload would restart paging
  // from the first page.
  const [removed, setRemoved] = useState<ReadonlySet<string>>(() => new Set());
  const [confirming, setConfirming] = useState<ChatStoredSession | null>(null);
  const remove = useMutation({
    mutationFn: (row: ChatStoredSession) => deleteChatSession(agentId, kind, row.session_id),
    onSuccess: (_data, row) => {
      setRemoved((prev) => new Set(prev).add(row.session_id));
      setConfirming(null);
    },
  });
  const rows = (sessions.data?.pages.flatMap((page) => page.sessions) ?? []).filter(
    (row) => !removed.has(row.session_id),
  );
  const canDelete = sessions.data?.pages[0]?.can_delete === true;
  const busy = opening !== null || remove.isPending;
  const closeConfirm = (): void => {
    setConfirming(null);
    remove.reset();
  };

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
        ) : rows.length === 0 && !sessions.hasNextPage ? (
          <p className="page-note">
            {scope === 'folder' ? 'No earlier sessions in this folder.' : 'No earlier sessions.'}
          </p>
        ) : (
          <ul className="resume-list">
            {rows.map((row) => {
              const when = row.updated_at === undefined ? null : formatRelativeTime(row.updated_at, now);
              return (
                <li key={row.session_id} className="resume-item">
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
                      <span className="resume-row-title">{rowTitle(row)}</span>
                      {scope === 'all' && <span className="resume-row-cwd">{row.cwd}</span>}
                      {row.open_elsewhere === true && row.open_session_id === undefined && (
                        <span className="resume-row-note">
                          Open in another app on your PC. Close it there first.
                        </span>
                      )}
                    </span>
                    {row.open_session_id !== undefined && <span className="chat-badge">Open</span>}
                    {when !== null && <span className="resume-row-time">{when}</span>}
                    {opening === row.session_id && <span className="chat-spinner" aria-hidden="true" />}
                  </button>
                  {canDeleteRow(canDelete, row) && (
                    <button
                      type="button"
                      className="button resume-row-delete"
                      disabled={busy}
                      aria-label={`Delete ${rowTitle(row)}`}
                      title="Delete"
                      onClick={() => {
                        remove.reset();
                        setConfirming(row);
                      }}
                    >
                      Delete
                    </button>
                  )}
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
      {confirming !== null && (
        <Modal
          role="alertdialog"
          labelledBy={confirmTitleId}
          dismissible={!remove.isPending}
          initialFocus={keepRef}
          onClose={closeConfirm}
        >
          <h2 id={confirmTitleId}>Delete this session?</h2>
          <p className="resume-confirm-title">{rowTitle(confirming)}</p>
          <p>{deleteNote(kind)}</p>
          <p className="page-note">
            If the session is still open in a terminal on your PC, close it there first.
          </p>
          {remove.isError && (
            <p className="field-error" role="alert">
              {remove.error.message}
            </p>
          )}
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={remove.isPending}
              aria-busy={remove.isPending}
              onClick={() => {
                remove.mutate(confirming);
              }}
            >
              {remove.isPending ? 'Deleting…' : 'Delete'}
            </button>
            <button
              type="button"
              ref={keepRef}
              className="button"
              disabled={remove.isPending}
              onClick={closeConfirm}
            >
              Keep
            </button>
          </div>
        </Modal>
      )}
    </Modal>
  );
}
