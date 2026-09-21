import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';
import {
  checkoutGitRef,
  createGitBranch,
  deleteGitItems,
  gitItemId,
  GitBusyError,
  isDeletable,
  KIND_REMOTE_BRANCH,
  listGitItems,
  type DeletableGitItem,
  type GitBranchCreateRequest,
  type GitItem,
} from '../../../api/gitRepository';
import { busyHolderLine } from '../../../api/conflicts';
import { isApiError } from '../../../api/errors';
import { queryKeys } from '../../../lib/queryKeys';
import { useDeferredCommit } from '../../../lib/useDeferredCommit';
import { NO_GIT_CAPABILITIES } from '../../../protocol/dto/agentVersion';
import type { BusyHolder } from '../../../protocol/dto/gitConflict';
import { useAgentEvents } from '../../../realtime';
import { useToastStore } from '../../../stores/toastStore';
import { ErrorState } from '../../../components/ErrorState';
import { Modal } from '../../../components/Modal';
import { UndoBar } from '../../../components/UndoBar';
import { useSessionGitCapabilities } from '../../terminal/git/useSessionGit';
import { GitFolderRow, GitItemRow } from './GitItemRow';
import { GitNewBranchDialog } from './GitNewBranchDialog';
import {
  FILTER_THRESHOLD,
  branchCreatedMessage,
  checkedOutMessage,
  checkoutConfirmMessage,
  checkoutErrorCopy,
  deleteFailureMessage,
  forceDeleteMessage,
  gitTreeRows,
  matchesFilter,
  primaryLabel,
  remoteDeleteMessage,
  type GitTreeRow,
} from './gitDisplay';
import {
  applyResults,
  confirmForce,
  dismissForce,
  enqueue,
  enqueueWorktreeRemoval,
  initialGitDeleteState,
  reconcile,
  restore,
  takePending,
  undo,
  visibleItems,
  type GitDeleteState,
  type PendingDelete,
} from './gitDeleteMachine';

/**
 * Branches and worktrees for one project as a folder tree (a branch name
 * splits on `/`), the remote-tracking branches in a collapsed section under
 * them, plus New branch and Check out once the agent advertises
 * `capabilities.git.branches` and remote deletes behind
 * `capabilities.git.delete_remote`.
 *
 * Delete is deferred: rows hide at once and the request goes out only after the
 * undo window closes, so Undo cancels before anything reaches the server. A
 * second delete inside the window joins the batch and restarts the countdown.
 * Check out and New branch are pessimistic instead: each is a confirmed or
 * typed request whose outcome the list shows through `git.changed`.
 *
 * Unlike the mobile models this needs no generation counter around loads:
 * `git.changed` arrives *before* the delete response, but the list lives in
 * TanStack Query, whose invalidation cancels an in-flight refetch on a warm
 * cache rather than letting a stale one land last.
 */
export function GitScreen() {
  const params = useParams();
  const agentId = params.agentId ?? '';
  const projectName = params.projectName ?? '';
  const queryClient = useQueryClient();

  const [machine, setMachine] = useState<GitDeleteState>(initialGitDeleteState);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [busyDialog, setBusyDialog] = useState<BusyHolder[] | null>(null);
  const [remoteOpen, setRemoteOpen] = useState(false);
  const [toggled, setToggled] = useState<ReadonlySet<string>>(new Set());
  const [remotePrompt, setRemotePrompt] = useState<DeletableGitItem[] | null>(null);
  const remoteCancelRef = useRef<HTMLButtonElement>(null);
  const [filter, setFilter] = useState('');
  const [newBranchOpen, setNewBranchOpen] = useState(false);
  const [checkoutPrompt, setCheckoutPrompt] = useState<GitItem | null>(null);
  const checkoutCancelRef = useRef<HTMLButtonElement>(null);

  // The delete callbacks run from a timer and from mutation results, both of
  // which would otherwise close over whichever state existed when they were
  // created. This is the web analogue of holding `pendingBatch` outside the
  // deferred task on iOS.
  const machineRef = useRef(machine);
  const forceCancelRef = useRef<HTMLButtonElement>(null);
  machineRef.current = machine;

  useAgentEvents(agentId);

  // The same probe the terminal uses: it is per agent, not per session.
  const capabilities =
    useSessionGitCapabilities(agentId, agentId !== '').data ?? NO_GIT_CAPABILITIES;
  const branchesEnabled = capabilities.branches;
  const deleteRemote = capabilities.deleteRemote;

  const items = useQuery({
    queryKey: queryKeys.gitItems(agentId, projectName),
    queryFn: () => listGitItems(agentId, projectName),
    enabled: agentId !== '' && projectName !== '',
    // Deliberately not `wsBackedQueryOptions`. `git.changed` is a pointer, not a
    // snapshot, so this cache is only ever repaired by a refetch — and an
    // invalidation landing during a *cold* fetch is deduped into it. Ordinary
    // staleness is the backstop for that window (CLA-234).
  });

  const del = useMutation({
    mutationFn: (batch: PendingDelete[]) =>
      deleteGitItems(
        agentId,
        projectName,
        batch.map((entry) => entry.item),
      ),
    onSuccess: (results, batch) => {
      const outcome = applyResults(machineRef.current, batch, results);
      setMachine(outcome.state);
      if (outcome.failures.length > 0) {
        useToastStore.getState().show(deleteFailureMessage(outcome.failures), 'error');
      }
      void queryClient.invalidateQueries({
        queryKey: queryKeys.gitItems(agentId, projectName),
      });
    },
    onError: (error: unknown, batch) => {
      // Every failure path restores the whole batch: the busy guard runs before
      // any deletion, so a 409 means nothing at all was processed.
      setMachine(restore(machineRef.current, batch));
      if (error instanceof GitBusyError) {
        if (error.holders !== null) setBusyDialog(error.holders);
        else useToastStore.getState().show('Some items are in use.', 'error');
        return;
      }
      useToastStore
        .getState()
        .show(
          isApiError(error) ? `Delete failed: ${error.message}` : 'Delete failed',
          'error',
        );
    },
  });

  const invalidateItems = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.gitItems(agentId, projectName) });

  const checkout = useMutation({
    mutationFn: (item: GitItem) => checkoutGitRef(agentId, projectName, item.branch ?? ''),
    onSuccess: (result) => {
      useToastStore.getState().show(checkedOutMessage(result), 'success');
      void invalidateItems();
    },
    onError: (error: unknown, item) => {
      if (error instanceof GitBusyError) {
        if (error.holders !== null) setBusyDialog(error.holders);
        else useToastStore.getState().show('The project folder is in use.', 'error');
        return;
      }
      useToastStore.getState().show(checkoutErrorCopy(error, primaryLabel(item)), 'error');
    },
  });

  const create = useMutation({
    mutationFn: (request: GitBranchCreateRequest) =>
      createGitBranch(agentId, projectName, request),
    onSuccess: () => void invalidateItems(),
  });

  const commitPending = () => {
    const { state, batch } = takePending(machineRef.current);
    setMachine(state);
    if (batch.length > 0) del.mutate(batch);
  };

  const deferred = useDeferredCommit<number>({
    onCommit: commitPending,
    // Coalescing rather than flushing: a second delete inside the window joins
    // the batch the machine already holds, and only the countdown restarts.
    merge: (_previous, next) => ({ commit: null, next }),
  });

  // Reconcile whenever a fresh list lands: rows the server already dropped stay
  // hidden-and-gone, anything still listed and no longer queued comes back.
  useEffect(() => {
    const data = items.data;
    if (data === undefined) return;
    setMachine((previous) => reconcile(previous, data));
  }, [items.data]);

  // A batch emptied by reconciliation has nothing left to send.
  const { pending: windowOpen, cancel: cancelWindow } = deferred;
  useEffect(() => {
    if (windowOpen !== null && machine.pending.length === 0) cancelWindow();
  }, [windowOpen, cancelWindow, machine.pending.length]);

  // Stable identities, so the memoised rows only re-render when their own item
  // or selection changes rather than on every keystroke elsewhere on the screen.
  const { schedule } = deferred;
  const startWindow = useCallback(
    (next: GitDeleteState) => {
      if (next === machineRef.current) return;
      setMachine(next);
      setSelected(new Set());
      schedule(next.pending.length);
    },
    [schedule],
  );

  // A remote delete reaches everyone using the remote, so a batch holding one
  // is confirmed before it enters the undo window.
  const queue = useCallback(
    (targets: readonly GitItem[]) => {
      const deletable = targets.filter((item) => isDeletable(item, deleteRemote));
      if (deletable.some((item) => item.kind === KIND_REMOTE_BRANCH)) {
        setRemotePrompt(deletable);
        return;
      }
      startWindow(enqueue(machineRef.current, deletable, deleteRemote));
    },
    [startWindow, deleteRemote],
  );

  const removeWorktree = useCallback(
    (target: GitItem) => {
      startWindow(enqueueWorktreeRemoval(machineRef.current, target));
    },
    [startWindow],
  );

  const toggleFolder = useCallback((id: string) => {
    setToggled((previous) => {
      const next = new Set(previous);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, []);

  const toggleSelected = useCallback((id: string, isSelected: boolean) => {
    setSelected((previous) => {
      const next = new Set(previous);
      if (isSelected) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const queueOne = useCallback(
    (target: GitItem) => {
      queue([target]);
    },
    [queue],
  );

  const askCheckout = useCallback((target: GitItem) => {
    setCheckoutPrompt(target);
  }, []);

  const visible = visibleItems(items.data ?? [], machine);
  const selectable = visible.filter((item) => isDeletable(item, deleteRemote));
  const selectedItems = selectable.filter((item) => selected.has(gitItemId(item)));
  const filterable = visible.length >= FILTER_THRESHOLD;
  // Text left in the field after the list shrank below the threshold is
  // invisible, so it must neither filter nor force folders open.
  const filterActive = filterable && filter.trim() !== '';
  const shown = filterActive ? visible.filter((item) => matchesFilter(item, filter)) : visible;
  const localItems = shown.filter((item) => item.kind !== KIND_REMOTE_BRANCH);
  const remoteItems = shown.filter((item) => item.kind === KIND_REMOTE_BRANCH);
  const localRows = gitTreeRows(localItems, 'local', toggled, filterActive);
  const remoteRows = gitTreeRows(remoteItems, 'remote', toggled, filterActive);
  const rowBusy = del.isPending || checkout.isPending;

  const renderRow = (row: GitTreeRow) =>
    row.type === 'folder' ? (
      <GitFolderRow key={row.id} folder={row} disabled={filterActive} onToggle={toggleFolder} />
    ) : (
      <GitItemRow
        key={gitItemId(row.item)}
        item={row.item}
        label={row.label}
        depth={row.depth}
        selected={selected.has(gitItemId(row.item))}
        busy={rowBusy}
        checkoutEnabled={branchesEnabled}
        deleteRemote={deleteRemote}
        onToggle={toggleSelected}
        onDelete={queueOne}
        onRemoveWorktree={removeWorktree}
        onCheckout={askCheckout}
      />
    );

  return (
    <section className="page">
      <header className="page-header">
        <h1>Git: {projectName}</h1>
        <div className="button-row">
          {branchesEnabled && (
            <button
              type="button"
              className="button button-primary"
              disabled={rowBusy}
              onClick={() => {
                setNewBranchOpen(true);
              }}
            >
              New branch…
            </button>
          )}
          <Link to="/projects">Back to projects</Link>
        </div>
      </header>

      {selectedItems.length > 0 && (
        <div className="sessions-toolbar">
          <span>{selectedItems.length} selected</span>
          <button
            type="button"
            className="button button-danger"
            onClick={() => {
              queue(selectedItems);
            }}
          >
            Delete selected
          </button>
          <button
            type="button"
            className="button"
            onClick={() => {
              setSelected(new Set());
            }}
          >
            Clear selection
          </button>
        </div>
      )}

      {items.isPending ? (
        <p className="page-note state-loading">Loading git items…</p>
      ) : items.isError ? (
        <ErrorState
          message="Failed to load git items."
          onRetry={() => void items.refetch()}
        />
      ) : visible.length === 0 ? (
        <p className="empty-state">No branches or worktrees.</p>
      ) : (
        <>
          {filterable && (
            <input
              type="search"
              className="field-input"
              value={filter}
              placeholder="Filter branches"
              aria-label="Filter branches"
              onChange={(event) => {
                setFilter(event.target.value);
              }}
            />
          )}
          {localItems.length === 0 && remoteItems.length === 0 ? (
            <p className="empty-state">No matching branches or worktrees.</p>
          ) : (
            localItems.length > 0 && <ul className="git-list">{localRows.map(renderRow)}</ul>
          )}
          {remoteItems.length > 0 && (
            <details
              className="advanced git-remote-section"
              open={remoteOpen || filterActive}
              onToggle={(event) => {
                setRemoteOpen(event.currentTarget.open);
              }}
            >
              <summary>Remote branches ({remoteItems.length})</summary>
              <ul className="git-list">{remoteRows.map(renderRow)}</ul>
            </details>
          )}
        </>
      )}

      {deferred.pending !== null && (
        <UndoBar
          message={`Deleting ${String(machine.pending.length)} item(s)…`}
          onUndo={() => {
            setMachine(undo(machineRef.current));
            deferred.cancel();
          }}
          onDismiss={deferred.commitNow}
        />
      )}

      {remotePrompt !== null && (
        <Modal
          role="alertdialog"
          labelledBy="remote-delete-title"
          initialFocus={remoteCancelRef}
          onClose={() => {
            setRemotePrompt(null);
          }}
        >
          <h2 id="remote-delete-title">Delete on the remote?</h2>
          <p>
            {remoteDeleteMessage(
              remotePrompt
                .filter((item) => item.kind === KIND_REMOTE_BRANCH)
                .map(primaryLabel),
            )}
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              onClick={() => {
                const targets = remotePrompt;
                setRemotePrompt(null);
                startWindow(enqueue(machineRef.current, targets, deleteRemote));
              }}
            >
              Delete
            </button>
            <button
              type="button"
              ref={remoteCancelRef}
              className="button"
              onClick={() => {
                setRemotePrompt(null);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}

      {machine.force !== null && (
        <Modal
          role="alertdialog"
          labelledBy="force-title"
          initialFocus={forceCancelRef}
          onClose={() => {
            setMachine(dismissForce(machineRef.current));
          }}
        >
          <h2 id="force-title">Force delete?</h2>
          <p>{forceDeleteMessage(machine.force.refs)}</p>
          <div className="button-row">
            <button
              type="button"
              className="button button-danger"
              onClick={() => {
                // Skips the undo window on purpose: the user has just confirmed
                // this exact set in a dialog naming it.
                const { state, batch } = confirmForce(machineRef.current);
                setMachine(state);
                if (batch.length > 0) del.mutate(batch);
              }}
            >
              Force delete
            </button>
            <button
              type="button"
              ref={forceCancelRef}
              className="button"
              onClick={() => {
                setMachine(dismissForce(machineRef.current));
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}

      {checkoutPrompt !== null && (
        <Modal
          role="alertdialog"
          labelledBy="checkout-title"
          initialFocus={checkoutCancelRef}
          onClose={() => {
            setCheckoutPrompt(null);
          }}
        >
          <h2 id="checkout-title">Check out {primaryLabel(checkoutPrompt)}?</h2>
          <p>{checkoutConfirmMessage(primaryLabel(checkoutPrompt), projectName)}</p>
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              onClick={() => {
                const target = checkoutPrompt;
                setCheckoutPrompt(null);
                checkout.mutate(target);
              }}
            >
              Check out
            </button>
            <button
              type="button"
              ref={checkoutCancelRef}
              className="button"
              onClick={() => {
                setCheckoutPrompt(null);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}

      {newBranchOpen && (
        <GitNewBranchDialog
          items={visible}
          projectName={projectName}
          busy={create.isPending}
          create={create.mutateAsync}
          onClose={() => {
            setNewBranchOpen(false);
          }}
          onCreated={(result) => {
            setNewBranchOpen(false);
            useToastStore.getState().show(branchCreatedMessage(result), 'success');
          }}
          onBusy={(holders) => {
            setNewBranchOpen(false);
            if (holders !== null) setBusyDialog(holders);
            else useToastStore.getState().show('The project folder is in use.', 'error');
          }}
        />
      )}

      {busyDialog !== null && (
        <Modal
          role="alertdialog"
          labelledBy="busy-title"
          onClose={() => {
            setBusyDialog(null);
          }}
        >
          <h2 id="busy-title">Item is in use</h2>
          <p>Stop the holder, then try again.</p>
          <ul className="busy-holders">
            {busyDialog.map((holder, index) => (
              <li key={`${holder.kind}-${String(index)}`}>{busyHolderLine(holder)}</li>
            ))}
          </ul>
          <div className="button-row">
            <button
              type="button"
              className="button button-primary"
              onClick={() => {
                setBusyDialog(null);
              }}
            >
              OK
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
