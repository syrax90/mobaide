import { useCallback, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';
import {
  deleteGitItems,
  gitItemId,
  GitBusyError,
  isDeletable,
  listGitItems,
  type GitItem,
} from '../../../api/gitRepository';
import { busyHolderLine } from '../../../api/conflicts';
import { isApiError } from '../../../api/errors';
import { queryKeys } from '../../../lib/queryKeys';
import { useDeferredCommit } from '../../../lib/useDeferredCommit';
import type { BusyHolder } from '../../../protocol/dto/gitConflict';
import { useAgentEvents } from '../../../realtime';
import { useToastStore } from '../../../stores/toastStore';
import { ErrorState } from '../../../components/ErrorState';
import { Modal } from '../../../components/Modal';
import { UndoBar } from '../../../components/UndoBar';
import { GitItemRow } from './GitItemRow';
import { deleteFailureMessage, forceDeleteMessage } from './gitDisplay';
import {
  applyResults,
  confirmForce,
  dismissForce,
  enqueue,
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
 * Branches and worktrees for one project.
 *
 * Delete is deferred: rows hide at once and the request goes out only after the
 * undo window closes, so Undo cancels before anything reaches the server. A
 * second delete inside the window joins the batch and restarts the countdown.
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

  // The delete callbacks run from a timer and from mutation results, both of
  // which would otherwise close over whichever state existed when they were
  // created. This is the web analogue of holding `pendingBatch` outside the
  // deferred task on iOS.
  const machineRef = useRef(machine);
  machineRef.current = machine;

  useAgentEvents(agentId);

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
  const queue = useCallback(
    (targets: readonly GitItem[]) => {
      const next = enqueue(machineRef.current, targets);
      if (next === machineRef.current) return;
      setMachine(next);
      setSelected(new Set());
      schedule(next.pending.length);
    },
    [schedule],
  );

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

  const visible = visibleItems(items.data ?? [], machine);
  const selectable = visible.filter(isDeletable);
  const selectedItems = selectable.filter((item) => selected.has(gitItemId(item)));

  return (
    <section className="page">
      <header className="page-header">
        <h1>Git: {projectName}</h1>
        <Link to="/projects">Back to projects</Link>
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
        <ul className="git-list">
          {visible.map((item) => (
            <GitItemRow
              key={gitItemId(item)}
              item={item}
              selected={selected.has(gitItemId(item))}
              busy={del.isPending}
              onToggle={toggleSelected}
              onDelete={queueOne}
            />
          ))}
        </ul>
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

      {machine.force !== null && (
        <Modal
          role="alertdialog"
          labelledBy="force-title"
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
