import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import {
  GIT_CODE_HOOK_FAILED,
  isSessionGitError,
  type SessionGitCommitRequest,
  type SessionGitCommitResult,
} from '../../../api/sessionGitRepository';
import { Modal } from '../../../components/Modal';
import { CheckboxField } from '../../../components/form/CheckboxField';
import { TextAreaField } from '../../../components/form/TextAreaField';
import { TextField } from '../../../components/form/TextField';
import {
  IDENTITY_FIX_COMMANDS,
  authorLine,
  commitButtonLabel,
  composeCommitMessage,
  mutationErrorCopy,
  splitCommitMessage,
} from './sessionGitDisplay';
import { useSessionGitCommitContext } from './useSessionGit';

interface GitCommitDialogProps {
  agentId: string;
  sessionId: string;
  /** From the list's `counts.staged`: the button's label, and the "nothing to commit" guard. */
  stagedCount: number;
  busy: boolean;
  commit: (request: SessionGitCommitRequest) => Promise<SessionGitCommitResult>;
  onClose: () => void;
  onCommitted: (result: SessionGitCommitResult) => void;
}

/**
 * The commit sheet: Summary + Description (sent as `subject\n\nbody`), the
 * author line from `commit/context`, Amend with the previous message
 * prefilled, and Skip hooks. A modal `<dialog>` over the manual popover: the
 * panel stays on the top layer, inert underneath.
 *
 * Enter in the Summary submits through the form; Ctrl/⌘+Enter submits from
 * the Description too — the app's first use of that chord, the one every
 * commit-message editor teaches.
 */
export function GitCommitDialog({
  agentId,
  sessionId,
  stagedCount,
  busy,
  commit,
  onClose,
  onCommitted,
}: GitCommitDialogProps) {
  const context = useSessionGitCommitContext(agentId, sessionId, true);
  const [summary, setSummary] = useState('');
  const [description, setDescription] = useState('');
  const [amend, setAmend] = useState(false);
  const [noVerify, setNoVerify] = useState(false);
  // What Amend poured into the fields, so switching it off can take back
  // exactly that and nothing the user typed since.
  const prefill = useRef<{ summary: string; description: string } | null>(null);
  const [error, setError] = useState<{ text: string; output: string | null } | null>(null);

  const author = context.data?.author ?? null;
  const head = context.data?.head ?? null;
  const contextFailed = context.error !== null && context.data === undefined;
  const submittable =
    !busy &&
    author !== null &&
    summary.trim() !== '' &&
    (stagedCount > 0 || amend);

  const toggleAmend = (checked: boolean): void => {
    setAmend(checked);
    if (head === null) return;
    if (checked) {
      if (summary.trim() === '' && description.trim() === '') {
        const previous = splitCommitMessage(head);
        prefill.current = previous;
        setSummary(previous.summary);
        setDescription(previous.description);
      }
      return;
    }
    const poured = prefill.current;
    prefill.current = null;
    if (poured !== null && summary === poured.summary && description === poured.description) {
      setSummary('');
      setDescription('');
    }
  };

  const submit = async (): Promise<void> => {
    if (!submittable) return;
    setError(null);
    try {
      const result = await commit({
        message: composeCommitMessage(summary, description),
        amend,
        no_verify: noVerify,
      });
      onCommitted(result);
    } catch (failure: unknown) {
      const hookOutput =
        isSessionGitError(failure) && failure.code === GIT_CODE_HOOK_FAILED
          ? failure.detailMessage
          : null;
      setError({ text: mutationErrorCopy(failure), output: hookOutput });
    }
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    void submit();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>): void => {
    if (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    void submit();
  };

  return (
    <Modal labelledBy="git-commit-title" dismissible={!busy} onClose={onClose}>
      <h2 id="git-commit-title">{amend ? 'Amend commit' : 'Commit'}</h2>
      <form className="git-commit-form" onSubmit={onSubmit} onKeyDown={onKeyDown}>
        {context.isPending && <p className="page-note state-loading">Loading…</p>}
        {contextFailed && (
          <div className="git-changes-stale" role="alert">
            <span>{mutationErrorCopy(context.error)}</span>
            <button
              type="button"
              className="button"
              onClick={() => {
                void context.refetch();
              }}
            >
              Retry
            </button>
          </div>
        )}
        {author !== null && <p className="git-commit-author">{authorLine(author)}</p>}
        {context.data !== undefined && author === null && (
          <div className="git-commit-identity" role="alert">
            <p className="field-error">
              Git doesn&apos;t know who you are on the agent machine. Run these there, then
              try again:
            </p>
            <pre className="git-commit-fix">{IDENTITY_FIX_COMMANDS.join('\n')}</pre>
          </div>
        )}
        <TextField
          label="Summary"
          value={summary}
          onChange={setSummary}
          placeholder="What changed"
          autoFocus
          disabled={busy}
        />
        <TextAreaField
          label="Description"
          value={description}
          onChange={setDescription}
          rows={4}
          placeholder="Optional details (Ctrl+Enter to commit)"
          className="git-commit-description"
          disabled={busy}
        />
        {head !== null && (
          <CheckboxField
            label="Amend previous commit"
            checked={amend}
            disabled={busy}
            onChange={toggleAmend}
            hint={`Rewrites ${head.short_oid} · ${head.subject}`}
          />
        )}
        <CheckboxField
          label="Skip hooks (--no-verify)"
          checked={noVerify}
          disabled={busy}
          onChange={setNoVerify}
        />
        {error !== null && (
          <div className="git-commit-error" role="alert">
            <p className="field-error">{error.text}</p>
            {error.output !== null && error.output !== '' && (
              <pre className="git-commit-output">{error.output}</pre>
            )}
          </div>
        )}
        {stagedCount === 0 && !amend && context.data !== undefined && (
          <p className="field-hint">Nothing is staged. Stage files first, or amend the previous commit.</p>
        )}
        <div className="button-row">
          <button type="submit" className="button button-primary" disabled={!submittable}>
            {busy ? 'Committing…' : commitButtonLabel(stagedCount, amend)}
          </button>
          <button type="button" className="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
