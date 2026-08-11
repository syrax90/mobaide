import { useState } from 'react';
import { Modal } from '../../components/Modal';
import { TextField } from '../../components/form/TextField';

interface AddAgentDialogProps {
  busy: boolean;
  onCreate: (name: string) => void;
  onCancel: () => void;
}

/**
 * Manual agent creation — the fallback for a host that cannot show a pairing
 * code (no terminal access, a headless CI box, an operator who missed it).
 *
 * The name is trimmed on submit and a blank one cannot be submitted at all,
 * matching both mobile clients: Android disables Create while the field is
 * blank, iOS no-ops the action and leaves the dialog open.
 */
export function AddAgentDialog({ busy, onCreate, onCancel }: AddAgentDialogProps) {
  const [name, setName] = useState('');
  const trimmed = name.trim();

  return (
    <Modal labelledBy="add-agent-title" dismissible={!busy} onClose={onCancel}>
      <h2 id="add-agent-title">Add agent</h2>
      <p>
        Creates an agent and shows its credentials once. Prefer pairing when the host
        can display a code — it never puts a token on your screen.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (trimmed === '' || busy) return;
          onCreate(trimmed);
        }}
      >
        <TextField
          label="Agent name"
          value={name}
          onChange={setName}
          autoFocus
          disabled={busy}
          placeholder="Workstation"
        />
        <div className="button-row">
          <button
            type="submit"
            className="button button-primary"
            disabled={trimmed === '' || busy}
          >
            {busy ? 'Creating…' : 'Create'}
          </button>
          <button type="button" className="button" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}
