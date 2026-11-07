import { useState, type FormEvent } from 'react';
import {
  GitBusyError,
  KIND_BRANCH_ONLY,
  KIND_BRANCH_WITH_WORKTREE,
  KIND_REMOTE_BRANCH,
  compareRefNames,
  type GitBranchCreateRequest,
  type GitBranchResult,
  type GitItem,
} from '../../../api/gitRepository';
import type { BusyHolder } from '../../../protocol/dto/gitConflict';
import { Modal } from '../../../components/Modal';
import { CheckboxField } from '../../../components/form/CheckboxField';
import { SelectField, type SelectOption } from '../../../components/form/SelectField';
import { TextField } from '../../../components/form/TextField';
import { createBranchErrorCopy, primaryLabel } from './gitDisplay';

interface GitNewBranchDialogProps {
  /** The listed rows: local branches then remote ones, each A→Z, feed the start-point picker. */
  items: readonly GitItem[];
  projectName: string;
  busy: boolean;
  create: (request: GitBranchCreateRequest) => Promise<GitBranchResult>;
  onClose: () => void;
  onCreated: (result: GitBranchResult) => void;
  /** A `busy_by` 409 on create-with-checkout: the screen owns the holders dialog. */
  onBusy: (holders: BusyHolder[] | null, request: GitBranchCreateRequest) => void;
}

/**
 * The New branch sheet: a name, a start point defaulting to the checked-out
 * branch, and an opt-in checkout in the project folder. `null` in the picker
 * is the agent's own default, `HEAD` of the project root.
 */
export function GitNewBranchDialog({
  items,
  projectName,
  busy,
  create,
  onClose,
  onCreated,
  onBusy,
}: GitNewBranchDialogProps) {
  const current = items.find((item) => item.is_current)?.branch ?? null;
  const [name, setName] = useState('');
  const [startPoint, setStartPoint] = useState<string | null>(current);
  const [checkout, setCheckout] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Only rows whose branch ref exists can start a branch: an orphan worktree
  // still names a branch git already deleted, and its label is a path.
  const byName = (a: GitItem, b: GitItem) => compareRefNames(a.branch ?? '', b.branch ?? '');
  const local = items
    .filter(
      (item) =>
        item.branch !== null &&
        (item.kind === KIND_BRANCH_ONLY || item.kind === KIND_BRANCH_WITH_WORKTREE),
    )
    .sort(byName);
  const remote = items.filter((item) => item.kind === KIND_REMOTE_BRANCH).sort(byName);
  const options: SelectOption[] = [
    { value: null, label: 'HEAD (current commit)' },
    ...local.map((item) => ({ value: item.branch ?? '', label: primaryLabel(item) })),
    ...remote.map((item) => ({ value: item.branch ?? '', label: `${primaryLabel(item)} (remote)` })),
  ];

  const submittable = !busy && name.trim() !== '';

  const submit = async (): Promise<void> => {
    if (!submittable) return;
    setError(null);
    const trimmed = name.trim();
    const request: GitBranchCreateRequest = {
      name: trimmed,
      ...(startPoint === null ? {} : { start_point: startPoint }),
      checkout,
    };
    try {
      const result = await create(request);
      onCreated(result);
    } catch (failure: unknown) {
      if (failure instanceof GitBusyError) {
        onBusy(failure.holders, request);
        return;
      }
      setError(createBranchErrorCopy(failure, trimmed));
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    void submit();
  };

  return (
    <Modal labelledBy="git-new-branch-title" dismissible={!busy} onClose={onClose}>
      <h2 id="git-new-branch-title">New branch</h2>
      <form className="git-commit-form" onSubmit={onSubmit}>
        <TextField
          label="Name"
          value={name}
          onChange={setName}
          placeholder="feature/login"
          autoFocus
          disabled={busy}
        />
        <SelectField
          label="Start point"
          value={startPoint}
          options={options}
          onChange={setStartPoint}
          disabled={busy}
        />
        <CheckboxField
          label={`Check out in the project folder of ${projectName}`}
          checked={checkout}
          disabled={busy}
          onChange={setCheckout}
          hint="Refused while a session runs in that folder."
        />
        {error !== null && (
          <div className="git-commit-error" role="alert">
            <p className="field-error">{error}</p>
          </div>
        )}
        <div className="button-row">
          <button type="submit" className="button button-primary" disabled={!submittable}>
            {busy ? 'Creating…' : 'Create'}
          </button>
          <button type="button" className="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
