import { useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listGitItems } from '../../../api/gitRepository';
import type {
  SessionGitRebaseResult,
  SessionGitStatus,
} from '../../../api/sessionGitRepository';
import { Modal } from '../../../components/Modal';
import { SelectField, type SelectOption } from '../../../components/form/SelectField';
import { TextField } from '../../../components/form/TextField';
import { queryKeys } from '../../../lib/queryKeys';
import {
  headLabel,
  rebaseBlocker,
  rebaseErrorCopy,
  rebaseErrorOutput,
  rebaseTargets,
} from './sessionGitDisplay';

interface GitRebaseDialogProps {
  agentId: string;
  /** The session's project: its branches feed the picker; without one the ref is typed. */
  projectName: string | null;
  status: SessionGitStatus | undefined;
  busy: boolean;
  rebase: (onto: string) => Promise<SessionGitRebaseResult>;
  onClose: () => void;
  onDone: (result: SessionGitRebaseResult, onto: string) => void;
}

/**
 * The Rebase sheet: pick the branch to replay this one on top of. A
 * project-backed session picks from the project's local and remote-tracking
 * branches (the same rows as the project Git screen), deliberately with no
 * preselection for a history rewrite; any other session — or a list that
 * failed to load — types the ref. What would make git refuse to start is
 * explained up front and keeps the button disabled; the agent still decides.
 */
export function GitRebaseDialog({
  agentId,
  projectName,
  status,
  busy,
  rebase,
  onClose,
  onDone,
}: GitRebaseDialogProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [typed, setTyped] = useState('');
  const [error, setError] = useState<{ text: string; output: string | null } | null>(null);
  const items = useQuery({
    queryKey: queryKeys.gitItems(agentId, projectName ?? ''),
    queryFn: () => listGitItems(agentId, projectName ?? ''),
    enabled: projectName !== null,
    meta: { silent: true },
  });

  const repo = status?.repo ?? null;
  const targets = items.data === undefined ? [] : rebaseTargets(items.data, repo?.head.branch);
  const picking = projectName !== null && !items.isError;
  const onto = picking ? picked : typed.trim() === '' ? null : typed.trim();
  const blocker = rebaseBlocker(status);
  const submittable = !busy && blocker === null && onto !== null;
  const options: SelectOption[] = [
    { value: null, label: items.isPending ? 'Loading branches…' : 'Choose a branch' },
    ...targets,
  ];

  const submit = async (): Promise<void> => {
    if (!submittable || onto === null) return;
    setError(null);
    try {
      onDone(await rebase(onto), onto);
    } catch (failure: unknown) {
      setError({ text: rebaseErrorCopy(failure), output: rebaseErrorOutput(failure) });
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    void submit();
  };

  return (
    <Modal labelledBy="git-rebase-title" dismissible={!busy} onClose={onClose}>
      <h2 id="git-rebase-title">{repo === null ? 'Rebase' : `Rebase ${headLabel(repo)}`}</h2>
      <form className="git-commit-form" onSubmit={onSubmit}>
        <p className="field-hint">Replays this branch&apos;s commits on top of the branch you pick.</p>
        {picking ? (
          <SelectField
            label="Onto"
            value={picked}
            options={options}
            onChange={setPicked}
            disabled={busy || items.isPending}
          />
        ) : (
          <TextField
            label="Onto"
            value={typed}
            onChange={setTyped}
            placeholder="Branch, tag or commit"
            autoFocus
            disabled={busy}
          />
        )}
        {picking && items.isSuccess && targets.length === 0 && (
          <p className="field-hint">No other branches in this project.</p>
        )}
        {blocker !== null && <p className="field-error">{blocker}</p>}
        {error !== null && (
          <div className="git-commit-error" role="alert">
            <p className="field-error">{error.text}</p>
            {error.output !== null && <pre className="git-commit-output">{error.output}</pre>}
          </div>
        )}
        <div className="button-row">
          <button type="submit" className="button button-primary" disabled={!submittable}>
            {busy ? 'Rebasing…' : 'Rebase'}
          </button>
          <button type="button" className="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
