import { useState } from 'react';
import { Modal } from '../../../components/Modal';
import { TextAreaField } from '../../../components/form/TextAreaField';
import { TextField } from '../../../components/form/TextField';
import { validateCustomAgentType } from '../../../api/customAgentTypeRepository';

interface CreateCustomTypeDialogProps {
  busy: boolean;
  error: string | null;
  onSubmit: (input: { name: string; command: string }) => void;
  onCancel: () => void;
}

/**
 * The account-scoped custom agent type editor's create half.
 *
 * The card that opens this is always enabled — the 20-per-account cap is not
 * checked client-side because the cached list can be stale, so the server's 409
 * is the authority and lands in `error`. Both mobile clients do the same.
 */
export function CreateCustomTypeDialog({
  busy,
  error,
  onSubmit,
  onCancel,
}: CreateCustomTypeDialogProps) {
  const [name, setName] = useState('');
  const [command, setCommand] = useState('');
  const [touched, setTouched] = useState(false);

  const validation = validateCustomAgentType(name, command);

  return (
    <Modal labelledBy="custom-type-title" dismissible={!busy} onClose={onCancel}>
      <h2 id="custom-type-title">Add custom agent</h2>
      <TextField
        label="Name"
        value={name}
        autoFocus
        error={touched && validation?.startsWith('Name') === true ? validation : undefined}
        onChange={setName}
      />
      <TextAreaField
        label="Launch command"
        value={command}
        placeholder="aider --model sonnet"
        hint="Runs in the chosen directory, like a terminal command."
        error={touched && validation?.startsWith('Command') === true ? validation : undefined}
        onChange={setCommand}
      />
      {error !== null && <p className="field-error">{error}</p>}
      <div className="button-row">
        <button
          type="button"
          className="button button-primary"
          disabled={busy}
          onClick={() => {
            setTouched(true);
            if (validation === null) onSubmit({ name: name.trim(), command: command.trim() });
          }}
        >
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}

interface DeleteCustomTypeDialogProps {
  name: string;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function DeleteCustomTypeDialog({
  name,
  busy,
  error,
  onConfirm,
  onCancel,
}: DeleteCustomTypeDialogProps) {
  return (
    <Modal
      role="alertdialog"
      labelledBy="delete-type-title"
      dismissible={!busy}
      onClose={onCancel}
    >
      <h2 id="delete-type-title">Delete agent type?</h2>
      <p>
        Delete &quot;{name}&quot;? Saved shortcuts for this agent type will also be deleted.
        Running sessions are not affected.
      </p>
      {error !== null && <p className="field-error">{error}</p>}
      <div className="button-row">
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? 'Deleting…' : 'Delete'}
        </button>
        <button type="button" className="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </Modal>
  );
}
