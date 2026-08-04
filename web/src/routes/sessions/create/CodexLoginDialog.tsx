import { useEffect, useRef, useState } from 'react';
import { PasswordField } from '../../../components/form/PasswordField';

interface CodexLoginDialogProps {
  busy: boolean;
  error: string | null;
  onSubmit: (apiKey: string) => void;
  onCancel: () => void;
}

/**
 * Codex is installed on the agent but not signed in.
 *
 * A native `<dialog>` opened with `showModal()`, unlike the terminal's
 * `role="dialog"` panel: this one blocks a submit in progress, so the free focus
 * trap and Esc handling are worth having.
 *
 * The key is never stored anywhere but this component's state — no query key, no
 * URL, no persisted store — and the agent pipes it to `codex login` over stdin
 * without persisting it either.
 */
export function CodexLoginDialog({ busy, error, onSubmit, onCancel }: CodexLoginDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [apiKey, setApiKey] = useState('');

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog !== null && !dialog.open) dialog.showModal();
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="dialog"
      aria-labelledby="codex-login-title"
      onCancel={(event) => {
        event.preventDefault();
        if (busy) return;
        setApiKey('');
        onCancel();
      }}
    >
      <form
        method="dialog"
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy && apiKey.trim() !== '') onSubmit(apiKey.trim());
        }}
      >
        <h2 id="codex-login-title">Codex needs sign-in</h2>
        <p>
          Codex isn&apos;t signed in on this agent. Enter an OpenAI API key to sign in — it&apos;s
          sent to the agent and never stored on your device.
        </p>

        <PasswordField
          label="API key"
          autoComplete="off"
          autoFocus
          value={apiKey}
          onChange={setApiKey}
          error={error ?? undefined}
        />

        <div className="button-row">
          <button
            type="submit"
            className="button button-primary"
            disabled={busy || apiKey.trim() === ''}
          >
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => {
              setApiKey('');
              onCancel();
            }}
          >
            Cancel
          </button>
        </div>
      </form>
    </dialog>
  );
}
