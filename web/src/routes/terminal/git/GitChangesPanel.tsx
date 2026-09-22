import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  GIT_CODE_JOB_RUNNING,
  isSessionGitError,
  isSessionGitJobLost,
  type SessionGitCommitResult,
  type SessionGitRebaseResult,
  type SessionGitSyncRequest,
} from '../../../api/sessionGitRepository';
import { Modal } from '../../../components/Modal';
import { useUnloadGuard } from '../../../keyboard/useUnloadGuard';
import { queryKeys } from '../../../lib/queryKeys';
import { NO_GIT_CAPABILITIES, hasGitSync } from '../../../protocol/dto/agentVersion';
import { GitChangesList } from './GitChangesList';
import { GitCommitDialog } from './GitCommitDialog';
import { GitConflictPane, type ConflictPaneNotice } from './GitConflictPane';
import { GitDiffPane } from './GitDiffPane';
import { GitRebaseDialog } from './GitRebaseDialog';
import { GitSyncDialog, SyncProgressBar } from './GitSyncDialog';
import {
  discardEditsPrompt,
  resolvedElsewhereNotice,
  restorePrompt,
  restoredNotice,
} from './sessionGitConflictDisplay';
import {
  DIFF_CONTEXT_DEFAULT,
  JOB_LOST_COPY,
  JOB_RESULT_DISMISS_MS,
  commitNotice,
  discardPrompt,
  discardScopeFor,
  jobNotice,
  jobTitle,
  mutationErrorCopy,
  opensResolver,
  pathsFor,
  rebaseBanner,
  rebaseErrorCopy,
  rebaseOutcomeNotice,
  rebaseStepPrompt,
  sectionsFor,
  type ChangesRow,
  type RebasePromptStep,
  type RowAction,
} from './sessionGitDisplay';
import {
  useSessionGitCapabilities,
  useSessionGitConflictMutations,
  useSessionGitJob,
  useSessionGitLatestJob,
  useSessionGitMutations,
  useSessionGitRebase,
  useSessionGitStatus,
  useSessionGitSync,
} from './useSessionGit';

interface GitChangesPanelProps {
  agentId: string;
  sessionId: string;
  /**
   * Bumped by the terminal menu's *Commit…*: each new value opens the commit
   * dialog, on a panel that may already be open.
   */
  commitRequest?: number;
  /** Bumped by the terminal menu's *Fetch / Pull / Push…*, the same way. */
  syncRequest?: number;
  /** Bumped by the terminal menu's *Rebase…*, the same way. */
  rebaseRequest?: number;
  /** The session's project, whose branches the rebase sheet offers; null types the ref. */
  projectName?: string | null;
  onClose: () => void;
}

interface PanelNotice {
  tone: 'ok' | 'danger';
  text: string;
}

/**
 * The in-session Changes viewer: a slide-over on the top layer with the file
 * list beside the diff (stacked below 900 px), plus the stage / unstage /
 * discard / commit actions of #675.
 *
 * `popover="manual"`, not a `<dialog>`: the terminal stays live underneath —
 * the point of a viewer next to the PTY that produces the changes — and a
 * non-modal `dialog.show()` is not on the top layer, where xterm's canvases
 * would hit-test above it. Manual, not auto, so a click into the terminal
 * does not dismiss it; Escape and the close button do, and the caller hands
 * focus back to the menu that opened it. Mounted only while open, the way
 * `Modal` is, so the poll below simply stops with it.
 *
 * Mutation outcomes render in the panel's own notice strip: `.toast-viewport`
 * paints below the top layer, so a toast raised here would never be seen.
 * A fetch / pull / push job (#676) gets a second strip of the same kind while
 * it runs and until its result is dismissed, and a rebase in progress a third,
 * with Continue / Skip / Abort, for as long as the status says `rebasing`.
 *
 * A conflict row opens the resolver in the diff's place. It is pinned by path,
 * not by the selected row: the file leaves *Conflicts* the moment it is
 * resolved — here, or by `git add` in the terminal — and an editor with unsaved
 * text must not vanish with its row. Every way out of a dirty editor (another
 * row, View diff, Escape, Close) asks first.
 */
export function GitChangesPanel({
  agentId,
  sessionId,
  commitRequest = 0,
  syncRequest = 0,
  rebaseRequest = 0,
  projectName = null,
  onClose,
}: GitChangesPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const cancelDiscardRef = useRef<HTMLButtonElement>(null);
  const cancelRebaseStepRef = useRef<HTMLButtonElement>(null);
  const cancelLeaveRef = useRef<HTMLButtonElement>(null);
  const cancelRestoreRef = useRef<HTMLButtonElement>(null);
  const queryClient = useQueryClient();
  const [selection, setSelection] = useState<ChangesRow | null>(null);
  const [context, setContext] = useState(DIFF_CONTEXT_DEFAULT);
  const [view, setView] = useState<'list' | 'diff'>('list');
  const [selectedKeys, setSelectedKeys] = useState<ReadonlySet<string>>(() => new Set());
  const [discardRows, setDiscardRows] = useState<readonly ChangesRow[] | null>(null);
  const [commitOpen, setCommitOpen] = useState(false);
  const [syncOpen, setSyncOpen] = useState(false);
  const [rebaseOpen, setRebaseOpen] = useState(false);
  const [pendingRebaseStep, setPendingRebaseStep] = useState<RebasePromptStep | null>(null);
  const [notice, setNotice] = useState<PanelNotice | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [conflictPath, setConflictPath] = useState<string | null>(null);
  const [conflictDirty, setConflictDirty] = useState(false);
  const [conflictDialogOpen, setConflictDialogOpen] = useState(false);
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  const [restorePath, setRestorePath] = useState<string | null>(null);
  const status = useSessionGitStatus(agentId, sessionId, true);
  const capabilities = useSessionGitCapabilities(agentId, true).data ?? NO_GIT_CAPABILITIES;
  const mutations = useSessionGitMutations(agentId, sessionId);
  const sync = useSessionGitSync(agentId, sessionId);
  const rebase = useSessionGitRebase(agentId, sessionId);
  const conflictMutations = useSessionGitConflictMutations(agentId, sessionId);
  useUnloadGuard(conflictDirty);

  // The job this panel follows. Adopted from wherever it surfaces — this
  // panel's own start, a socket frame for a job another client started, or
  // the id a `409 job_running` names — and let go of by dismissing its
  // result. A dismissed id is remembered so the pointer does not re-adopt it.
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [dismissedJobId, setDismissedJobId] = useState<string | null>(null);
  const latestJob = useSessionGitLatestJob(agentId, sessionId).data;
  useEffect(() => {
    const jobId = latestJob?.jobId;
    if (jobId === undefined || jobId === dismissedJobId) return;
    setActiveJobId(jobId);
  }, [latestJob?.jobId, dismissedJobId]);
  const jobQuery = useSessionGitJob(agentId, sessionId, activeJobId);
  const job = activeJobId === null ? undefined : jobQuery.data;
  const jobLost = activeJobId !== null && isSessionGitJobLost(jobQuery.error);
  // Between the start and its answer, and while a job runs, the staged set
  // is off limits (the agent answers `job_running`); the sync controls and
  // navigation are not. A job the agent has forgotten holds nothing.
  const jobRunning = !jobLost && job !== undefined && job.status !== 'done';
  // What holds the repository apart from the resolver's own requests, which
  // the resolver pane counts itself.
  const repoBusy = mutations.busy || rebase.busy || sync.start.isPending || jobRunning;
  const busy = repoBusy || conflictMutations.busy;

  const dismissJob = (): void => {
    if (activeJobId !== null) setDismissedJobId(activeJobId);
    setActiveJobId(null);
  };

  useLayoutEffect(() => {
    const panel = panelRef.current;
    panel?.showPopover();
    closeRef.current?.focus();
    return () => {
      panel?.hidePopover();
    };
  }, []);

  useEffect(() => {
    if (commitRequest > 0) setCommitOpen(true);
  }, [commitRequest]);

  useEffect(() => {
    if (syncRequest > 0) setSyncOpen(true);
  }, [syncRequest]);

  useEffect(() => {
    if (rebaseRequest > 0) setRebaseOpen(true);
  }, [rebaseRequest]);

  // A successful job clears its own banner; a failure stays until dismissed,
  // because its follow-ups live behind it.
  const jobDoneOk = job !== undefined && job.status === 'done' && job.ok === true;
  const jobDoneId = jobDoneOk ? job.job_id : null;
  useEffect(() => {
    if (jobDoneId === null) return;
    const timer = setTimeout(() => {
      setDismissedJobId(jobDoneId);
      setActiveJobId((current) => (current === jobDoneId ? null : current));
    }, JOB_RESULT_DISMISS_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [jobDoneId]);

  // After a commit the dialog's opener is the now-disabled Commit… button, so
  // the native close algorithm has nothing to hand focus back to and it would
  // fall to the body — taking Escape away from the panel. The sync sheet's
  // opener can be disabled the same way, and a rebase step's button vanishes
  // with the banner once the rebase is over. A passive effect, because the
  // dialog closes from a layout-effect cleanup.
  const [focusCloseAfterDialog, setFocusCloseAfterDialog] = useState(false);
  const dialogOpen =
    commitOpen ||
    syncOpen ||
    rebaseOpen ||
    pendingRebaseStep !== null ||
    pendingLeave !== null ||
    restorePath !== null ||
    conflictDialogOpen;
  useEffect(() => {
    if (!focusCloseAfterDialog || dialogOpen) return;
    closeRef.current?.focus();
    setFocusCloseAfterDialog(false);
  }, [focusCloseAfterDialog, dialogOpen]);

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
  // into the PTY beside the panel, or this panel's own Stage — or drop it.
  // Follow it by path so the highlight and the diff's scope track the list,
  // and let go once it is gone. Ticked rows that vanished are dropped too.
  const body = status.data;
  useEffect(() => {
    if (body === undefined) return;
    const rows = sectionsFor(body).flatMap((section) => section.rows);
    setSelectedKeys((previous) => {
      if (previous.size === 0) return previous;
      const kept = new Set([...previous].filter((key) => rows.some((row) => row.key === key)));
      return kept.size === previous.size ? previous : kept;
    });
    if (selection === null) return;
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
    if (next === null && conflictPath === null) setView('list');
  }, [body, selection, conflictPath]);

  // The resolver's file left *Conflicts* without this pane's doing. With no
  // unsaved text there is nothing to keep open; with some, the pane stays and
  // says so itself.
  const conflictStillOpen =
    conflictPath !== null &&
    (body?.entries ?? []).some(
      (entry) => entry.group === 'conflicts' && entry.path === conflictPath,
    );
  useEffect(() => {
    if (conflictPath === null || body === undefined || conflictStillOpen || conflictDirty) return;
    setNotice({ tone: 'ok', text: resolvedElsewhereNotice(conflictPath) });
    setConflictPath(null);
    setView('list');
  }, [conflictPath, body, conflictStillOpen, conflictDirty]);

  /** Run `action` now, or after the user agrees to drop the resolver's unsaved text. */
  const guardLeave = (action: () => void): void => {
    if (conflictPath !== null && conflictDirty) setPendingLeave(() => action);
    else action();
  };

  const closeResolver = (): void => {
    setConflictPath(null);
    setConflictDirty(false);
  };

  const onConflictDone = (result: ConflictPaneNotice): void => {
    closeResolver();
    setSelection(null);
    setView('list');
    setNotice(result);
    setFocusCloseAfterDialog(true);
  };

  const onConflictDirty = useCallback((dirty: boolean): void => {
    setConflictDirty(dirty);
  }, []);
  const onConflictDialog = useCallback((open: boolean): void => {
    setConflictDialogOpen(open);
  }, []);

  const restoreConflict = async (path: string): Promise<void> => {
    setRestorePath(null);
    setNotice(null);
    try {
      await conflictMutations.restore.mutateAsync({ path, style: 'merge' });
      setNotice({ tone: 'ok', text: restoredNotice(path) });
    } catch (error: unknown) {
      setNotice({ tone: 'danger', text: mutationErrorCopy(error) });
    }
    setFocusCloseAfterDialog(true);
  };

  const refresh = (): void => {
    void status.refetch();
    invalidateDiffs();
  };

  // A top-layer <dialog> still bubbles its keydown to this DOM parent, so an
  // Escape meant for the commit sheet or the discard confirmation must not
  // also tear the panel down; the dialogs handle their own Escape.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'Escape') return;
    // The resolver's ⋯ menu closes itself on Escape and marks the event handled.
    if (event.defaultPrevented) return;
    if (dialogOpen || discardRows !== null) return;
    event.preventDefault();
    guardLeave(onClose);
  };

  const startSync = async (request: SessionGitSyncRequest): Promise<void> => {
    setSyncError(null);
    try {
      const started = await sync.start.mutateAsync(request);
      setDismissedJobId(null);
      setActiveJobId(started.job_id);
    } catch (error: unknown) {
      // Another client got there first: follow its job instead of failing.
      if (
        isSessionGitError(error) &&
        error.code === GIT_CODE_JOB_RUNNING &&
        error.detailMessage !== null &&
        error.detailMessage !== ''
      ) {
        setDismissedJobId(null);
        setActiveJobId(error.detailMessage);
      }
      setSyncError(mutationErrorCopy(error));
    }
  };

  const cancelSync = (jobId: string): void => {
    setSyncError(null);
    sync.cancel.mutate(jobId, {
      onError: (error: unknown) => {
        setSyncError(mutationErrorCopy(error));
      },
    });
  };

  const openSync = (): void => {
    setSyncError(null);
    setSyncOpen(true);
  };

  const closeSync = (): void => {
    setSyncOpen(false);
    setFocusCloseAfterDialog(true);
  };

  const toggleRow = (row: ChangesRow, selected: boolean): void => {
    setSelectedKeys((previous) => {
      const next = new Set(previous);
      if (selected) next.add(row.key);
      else next.delete(row.key);
      return next;
    });
  };

  const forget = (rows: readonly ChangesRow[]): void => {
    setSelectedKeys((previous) => {
      const next = new Set(previous);
      for (const row of rows) next.delete(row.key);
      return next;
    });
  };

  const fail = (error: unknown): void => {
    setNotice({ tone: 'danger', text: mutationErrorCopy(error) });
  };

  const stage = async (rows: readonly ChangesRow[]): Promise<void> => {
    setNotice(null);
    try {
      await mutations.stage.mutateAsync(pathsFor(rows));
      forget(rows);
    } catch (error: unknown) {
      fail(error);
    }
  };

  const unstage = async (rows: readonly ChangesRow[]): Promise<void> => {
    setNotice(null);
    try {
      await mutations.unstage.mutateAsync(pathsFor(rows));
      forget(rows);
    } catch (error: unknown) {
      fail(error);
    }
  };

  // One request per scope: a selection can mix modified and untracked files,
  // and the route takes one scope at a time.
  const discard = async (rows: readonly ChangesRow[]): Promise<void> => {
    setDiscardRows(null);
    setNotice(null);
    const untracked = rows.filter((row) => discardScopeFor(row.entry) === 'untracked');
    const tracked = rows.filter((row) => discardScopeFor(row.entry) === 'unstaged');
    try {
      if (tracked.length > 0) {
        await mutations.discard.mutateAsync({ paths: pathsFor(tracked), scope: 'unstaged' });
      }
      if (untracked.length > 0) {
        await mutations.discard.mutateAsync({ paths: pathsFor(untracked), scope: 'untracked' });
      }
      forget(rows);
    } catch (error: unknown) {
      fail(error);
    }
  };

  const onAction = (action: RowAction, rows: readonly ChangesRow[]): void => {
    if (rows.length === 0) return;
    if (action === 'stage' || action === 'resolve') void stage(rows);
    else if (action === 'unstage') void unstage(rows);
    else setDiscardRows(rows);
  };

  const onCommitted = (result: SessionGitCommitResult): void => {
    setCommitOpen(false);
    setFocusCloseAfterDialog(true);
    setNotice({ tone: 'ok', text: commitNotice(result) });
  };

  // The branch name is read before the call: a finished rebase's status no
  // longer carries the rebase, and HEAD is detached while it runs.
  const rebasedBranch = (): string =>
    body?.repo?.rebase?.branch ?? body?.repo?.head.branch ?? 'the branch';

  const onRebased = (result: SessionGitRebaseResult, onto: string): void => {
    setRebaseOpen(false);
    setFocusCloseAfterDialog(true);
    setNotice(rebaseOutcomeNotice(result, rebasedBranch(), onto));
  };

  const runRebaseStep = async (step: 'continue' | 'skip' | 'abort'): Promise<void> => {
    setPendingRebaseStep(null);
    setNotice(null);
    const branch = rebasedBranch();
    try {
      setNotice(rebaseOutcomeNotice(await rebase.step.mutateAsync(step), branch, null));
    } catch (error: unknown) {
      setNotice({ tone: 'danger', text: rebaseErrorCopy(error) });
    }
    setFocusCloseAfterDialog(true);
  };

  const stagedCount = body?.counts?.staged ?? 0;
  const conflictCount = body?.counts?.conflicts ?? 0;
  const banner = rebaseBanner(body?.repo, conflictCount, capabilities.conflicts);
  const leavePrompt = conflictPath === null ? null : discardEditsPrompt(conflictPath);
  const restoreCopy = restorePath === null ? null : restorePrompt(restorePath);
  const stepPrompt =
    pendingRebaseStep === null ? null : rebaseStepPrompt(pendingRebaseStep, body?.repo);
  const prompt = discardRows === null ? null : discardPrompt(discardRows);
  const canSync = hasGitSync(capabilities);
  const jobTone = job === undefined || jobRunning ? 'busy' : job.ok === true ? 'ok' : 'danger';

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
        {capabilities.commit && (
          <button
            type="button"
            className="button button-primary"
            disabled={busy || stagedCount === 0}
            title={stagedCount === 0 ? 'Stage files to commit them' : 'Commit the staged files'}
            onClick={() => {
              setCommitOpen(true);
            }}
          >
            Commit…
          </button>
        )}
        {canSync && (
          <button
            type="button"
            className="button"
            title="Fetch, pull or push"
            onClick={openSync}
          >
            Sync…
          </button>
        )}
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
          onClick={() => {
            guardLeave(onClose);
          }}
        >
          ✕
        </button>
      </div>
      {activeJobId !== null && (job !== undefined || jobLost) && (
        <div
          className="git-panel-notice git-panel-job"
          data-tone={jobLost ? 'danger' : jobTone}
          role={jobTone === 'danger' || jobLost ? 'alert' : 'status'}
          aria-live="polite"
        >
          <span className="git-panel-job-body">
            <span>
              {jobLost
                ? JOB_LOST_COPY
                : job === undefined
                  ? 'Syncing…'
                  : jobNotice(job, capabilities.conflicts)}
            </span>
            {job !== undefined && jobRunning && (
              <>
                <SyncProgressBar percent={job.percent ?? null} label={job.phase ?? jobTitle(job.op)} />
                {job.line !== null && job.line !== undefined && job.line !== '' && (
                  <code className="git-sync-line">{job.line}</code>
                )}
              </>
            )}
          </span>
          {job !== undefined && jobRunning && (
            <button
              type="button"
              className="button"
              disabled={sync.cancel.isPending}
              onClick={() => {
                cancelSync(job.job_id);
              }}
            >
              Cancel
            </button>
          )}
          {job !== undefined && !jobRunning && job.ok === false && canSync && (
            <button type="button" className="button" onClick={openSync}>
              Options…
            </button>
          )}
          {(!jobRunning || jobLost) && (
            <button
              type="button"
              className="button button-icon"
              aria-label="Dismiss"
              title="Dismiss"
              onClick={dismissJob}
            >
              ✕
            </button>
          )}
        </div>
      )}
      {banner !== null && (
        <div className="git-panel-notice git-panel-rebase" data-tone="busy" role="status">
          <span className="git-panel-job-body">
            <strong>{banner.title}</strong>
            {banner.progress !== null && <span className="git-rebase-progress">{banner.progress}</span>}
            <span>{banner.hint}</span>
          </span>
          {capabilities.rebase && banner.actionable && (
            <>
              <button
                type="button"
                className="button button-primary"
                disabled={busy || conflictCount > 0}
                title={
                  conflictCount === 0
                    ? 'Continue the rebase'
                    : capabilities.conflicts
                      ? 'Resolve every conflict first'
                      : 'Mark every conflict resolved first'
                }
                onClick={() => {
                  void runRebaseStep('continue');
                }}
              >
                Continue
              </button>
              <button
                type="button"
                className="button"
                disabled={busy}
                onClick={() => {
                  setPendingRebaseStep('skip');
                }}
              >
                Skip…
              </button>
              <button
                type="button"
                className="button button-danger"
                disabled={busy}
                onClick={() => {
                  setPendingRebaseStep('abort');
                }}
              >
                Abort…
              </button>
            </>
          )}
        </div>
      )}
      {notice !== null && (
        <div
          className="git-panel-notice"
          data-tone={notice.tone}
          role={notice.tone === 'danger' ? 'alert' : 'status'}
        >
          <span>{notice.text}</span>
          <button
            type="button"
            className="button button-icon"
            aria-label="Dismiss"
            title="Dismiss"
            onClick={() => {
              setNotice(null);
            }}
          >
            ✕
          </button>
        </div>
      )}
      <div className="git-panel-body" data-view={view}>
        <div className="git-panel-list">
          <GitChangesList
            status={status.data}
            error={status.error}
            selectedKey={selection?.key ?? null}
            onSelect={(row) => {
              // The open resolver's own row: nothing is left, so nothing is
              // guarded — a confirmed "Discard" would otherwise clear the dirty
              // flag of a pane that never remounts and still holds the draft.
              if (conflictPath === row.entry.path && opensResolver(row.entry, capabilities)) {
                setSelection(row);
                setView('diff');
                return;
              }
              guardLeave(() => {
                closeResolver();
                setSelection(row);
                if (opensResolver(row.entry, capabilities)) setConflictPath(row.entry.path);
                setView('diff');
              });
            }}
            onRestore={
              capabilities.conflicts
                ? (path) => {
                    setRestorePath(path);
                  }
                : undefined
            }
            onRetry={() => {
              void status.refetch();
            }}
            capabilities={capabilities}
            busy={busy}
            selectedKeys={selectedKeys}
            onToggleRow={toggleRow}
            onClearSelection={() => {
              setSelectedKeys(new Set());
            }}
            onAction={onAction}
            onSync={canSync ? openSync : undefined}
          />
        </div>
        <div className="git-panel-diff">
          {conflictPath !== null ? (
            <GitConflictPane
              // A fresh mount per session and file, like the diff's.
              key={JSON.stringify([agentId, sessionId, conflictPath])}
              agentId={agentId}
              sessionId={sessionId}
              path={conflictPath}
              conflicted={conflictStillOpen}
              busy={repoBusy}
              onBack={() => {
                guardLeave(() => {
                  closeResolver();
                  setView('list');
                });
              }}
              onViewDiff={() => {
                guardLeave(closeResolver);
              }}
              onDirtyChange={onConflictDirty}
              onDone={onConflictDone}
              onNotice={setNotice}
              onDialogChange={onConflictDialog}
            />
          ) : (
            <GitDiffPane
              agentId={agentId}
              sessionId={sessionId}
              selection={selection}
              context={context}
              onContextChange={setContext}
              onBack={() => {
                setView('list');
              }}
              capabilities={capabilities}
              busy={busy}
              onAction={onAction}
              onResolve={(row) => {
                setConflictPath(row.entry.path);
              }}
            />
          )}
        </div>
      </div>
      {discardRows !== null && prompt !== null && (
        <Modal
          role="alertdialog"
          labelledBy="git-discard-title"
          dismissible={!mutations.busy}
          initialFocus={cancelDiscardRef}
          onClose={() => {
            setDiscardRows(null);
          }}
        >
          <h2 id="git-discard-title">{prompt.title}</h2>
          <p>{prompt.body}</p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={mutations.busy}
              onClick={() => {
                void discard(discardRows);
              }}
            >
              Discard
            </button>
            <button
              type="button"
              ref={cancelDiscardRef}
              className="button"
              disabled={mutations.busy}
              onClick={() => {
                setDiscardRows(null);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}
      {pendingLeave !== null && leavePrompt !== null && (
        <Modal
          role="alertdialog"
          labelledBy="git-conflict-leave-title"
          initialFocus={cancelLeaveRef}
          onClose={() => {
            setPendingLeave(null);
          }}
        >
          <h2 id="git-conflict-leave-title">{leavePrompt.title}</h2>
          <p>{leavePrompt.body}</p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              onClick={() => {
                const leave = pendingLeave;
                setPendingLeave(null);
                setConflictDirty(false);
                leave();
              }}
            >
              Discard
            </button>
            <button
              type="button"
              ref={cancelLeaveRef}
              className="button"
              onClick={() => {
                setPendingLeave(null);
              }}
            >
              Keep editing
            </button>
          </div>
        </Modal>
      )}
      {restorePath !== null && restoreCopy !== null && (
        <Modal
          role="alertdialog"
          labelledBy="git-conflict-restore-title"
          dismissible={!conflictMutations.busy}
          initialFocus={cancelRestoreRef}
          onClose={() => {
            setRestorePath(null);
          }}
        >
          <h2 id="git-conflict-restore-title">{restoreCopy.title}</h2>
          <p>{restoreCopy.body}</p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={conflictMutations.busy}
              onClick={() => {
                void restoreConflict(restorePath);
              }}
            >
              {restoreCopy.confirm}
            </button>
            <button
              type="button"
              ref={cancelRestoreRef}
              className="button"
              disabled={conflictMutations.busy}
              onClick={() => {
                setRestorePath(null);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}
      {commitOpen && (
        <GitCommitDialog
          agentId={agentId}
          sessionId={sessionId}
          stagedCount={stagedCount}
          busy={mutations.commit.isPending}
          commit={(request) => mutations.commit.mutateAsync(request)}
          onClose={() => {
            setCommitOpen(false);
          }}
          onCommitted={onCommitted}
        />
      )}
      {pendingRebaseStep !== null && stepPrompt !== null && (
        <Modal
          role="alertdialog"
          labelledBy="git-rebase-step-title"
          dismissible={!rebase.busy}
          initialFocus={cancelRebaseStepRef}
          onClose={() => {
            setPendingRebaseStep(null);
          }}
        >
          <h2 id="git-rebase-step-title">{stepPrompt.title}</h2>
          <p>{stepPrompt.body}</p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              disabled={rebase.busy}
              onClick={() => {
                void runRebaseStep(pendingRebaseStep);
              }}
            >
              {stepPrompt.confirm}
            </button>
            <button
              type="button"
              ref={cancelRebaseStepRef}
              className="button"
              disabled={rebase.busy}
              onClick={() => {
                setPendingRebaseStep(null);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}
      {rebaseOpen && (
        <GitRebaseDialog
          agentId={agentId}
          projectName={projectName}
          status={body}
          busy={rebase.start.isPending}
          rebase={(onto) => rebase.start.mutateAsync(onto)}
          onClose={() => {
            setRebaseOpen(false);
          }}
          onDone={onRebased}
        />
      )}
      {syncOpen && (
        <GitSyncDialog
          repo={body?.repo}
          capabilities={capabilities}
          job={job}
          jobLost={jobLost}
          starting={sync.start.isPending}
          startError={syncError}
          onStart={(request) => {
            void startSync(request);
          }}
          onCancelJob={cancelSync}
          onClose={closeSync}
        />
      )}
    </div>
  );
}
