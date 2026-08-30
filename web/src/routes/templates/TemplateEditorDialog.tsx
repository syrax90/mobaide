import { useState } from 'react';
import { Modal } from '../../components/Modal';
import { TextField } from '../../components/form/TextField';
import { TextAreaField } from '../../components/form/TextAreaField';
import type { PromptTemplate } from '../../api/templateRepository';

interface TemplateEditorDialogProps {
  /** `null` for a new template. */
  editing: PromptTemplate | null;
  /**
   * Seeds the body of a *new* template; ignored when `editing` is set, which
   * carries its own. The terminal's picker fills this from the xterm selection.
   * A synthetic `editing` row would seed the same field and wrongly flip the
   * title to "Edit template".
   */
  initialBody?: string;
  busy: boolean;
  error: string | null;
  onSave: (name: string, body: string) => void;
  onCancel: () => void;
}

/**
 * Create / edit a prompt template.
 *
 * The draft is held here and the dialog closes only on success, so a failed save
 * never discards a body the user was part-way through writing. iOS guards this;
 * Android does not, and the draft is the expensive thing to lose.
 *
 * Two callers: `/templates` manages the whole list, and the terminal's
 * `TemplatesPicker` creates one mid-session with `initialBody` pre-filled from
 * the selection.
 */
export function TemplateEditorDialog({
  editing,
  initialBody,
  busy,
  error,
  onSave,
  onCancel,
}: TemplateEditorDialogProps) {
  const [name, setName] = useState(editing?.name ?? '');
  const [body, setBody] = useState(editing?.body ?? initialBody ?? '');

  // The name is trimmed before the emptiness check; the body is checked and sent
  // verbatim, because a saved body reaches the PTY unchanged and trailing
  // whitespace can be load-bearing.
  const canSave = name.trim() !== '' && body !== '' && !busy;
  const titleId = 'template-editor-title';

  return (
    <Modal labelledBy={titleId} dismissible={!busy} onClose={onCancel}>
      <h2 id={titleId}>{editing === null ? 'New template' : 'Edit template'}</h2>

      <TextField label="Name" value={name} onChange={setName} autoFocus />
      <TextAreaField
        label="Prompt body"
        value={body}
        onChange={setBody}
        rows={8}
        className="template-body-input"
        autoCapitalize="off"
        autoCorrect="off"
      />

      {error !== null && <p className="field-error">{error}</p>}

      <div className="button-row">
        <button
          type="button"
          className="button button-primary"
          disabled={!canSave}
          onClick={() => {
            onSave(name.trim(), body);
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
