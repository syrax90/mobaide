import { useRef, useState } from 'react';
import { Modal } from '../../components/Modal';
import { CheckboxField } from '../../components/form/CheckboxField';

interface StopSessionDialogProps {
  /** `sessionDialogLabel` of the target — never its id. */
  label: string;
  /** The session's `worktree_branch`; the removal offer shows only when set. */
  worktreeBranch?: string | null;
  /** Another session works in that worktree: the removal is shown but disabled. */
  worktreeInUse?: boolean;
  busy: boolean;
  onStop: (force: boolean, removeWorktree: boolean) => void;
  onCancel: () => void;
}

/**
 * iOS's confirm-with-Force-Stop sheet. Android kills unconfirmed, which is a
 * worse trade on a desktop-sized click target than on a phone.
 */
export function StopSessionDialog({
  label,
  worktreeBranch,
  worktreeInUse = false,
  busy,
  onStop,
  onCancel,
}: StopSessionDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  // Never pre-checked: the removal throws away uncommitted and unmerged work.
  const [removeWorktree, setRemoveWorktree] = useState(false);
  const offerRemoval = worktreeBranch !== undefined && worktreeBranch !== null && worktreeBranch !== '';
  const remove = offerRemoval && !worktreeInUse && removeWorktree;
  return (
    <Modal
      role="alertdialog"
      labelledBy="stop-session-title"
      dismissible={!busy}
      initialFocus={cancelRef}
      onClose={onCancel}
    >
      <h2 id="stop-session-title">Stop session?</h2>
      <p>Stop &quot;{label}&quot;? Force Stop kills the process immediately.</p>
      {offerRemoval && (
        <CheckboxField
          label={worktreeRemovalLabel(worktreeBranch)}
          hint={worktreeInUse ? WORKTREE_IN_USE_HINT : WORKTREE_REMOVAL_HINT}
          checked={remove}
          disabled={busy || worktreeInUse}
          onChange={setRemoveWorktree}
        />
      )}
      <div className="button-row">
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            onStop(false, remove);
          }}
        >
          Stop
        </button>
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            onStop(true, remove);
          }}
        >
          Force Stop
        </button>
        <button type="button" ref={cancelRef} className="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

export function worktreeRemovalLabel(branch: string): string {
  return `Also remove the worktree and local branch "${branch}"`;
}

export const WORKTREE_REMOVAL_HINT =
  'Uncommitted changes and commits that are only on this branch are lost.';

export const WORKTREE_IN_USE_HINT =
  'Another session runs in this worktree. Stop it first to remove the worktree.';
