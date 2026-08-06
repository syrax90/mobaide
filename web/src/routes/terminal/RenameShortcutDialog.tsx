import { useState } from 'react';
import { clampShortcutName, type ShortcutButton } from '../../api/shortcutRepository';
import { TextField } from '../../components/form/TextField';
import { chordDisplay } from '../../protocol/keyChord';

interface RenameShortcutDialogProps {
  shortcut: ShortcutButton;
  busy: boolean;
  onSave: (name: string) => void;
  onClose: () => void;
}

/**
 * Set or clear a saved shortcut's label.
 *
 * Submitting an empty field is how a name is **cleared**: the wire sentinel is
 * `""`, never null, and the button falls back to the chord itself. The input is
 * clamped in the server's unit (code points) rather than by `slice`, which
 * counts UTF-16 units and would let an astral-character name through to a 422 —
 * `clampShortcutName` handles typing and paste alike, so no `maxLength` is
 * needed on top.
 */
export function RenameShortcutDialog({
  shortcut,
  busy,
  onSave,
  onClose,
}: RenameShortcutDialogProps) {
  const [name, setName] = useState(shortcut.name ?? '');

  return (
    <div className="confirm-panel" role="dialog" aria-labelledby="rename-shortcut-title">
      <h3 id="rename-shortcut-title">Name {chordDisplay(shortcut.chord)}</h3>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSave(name.trim());
        }}
      >
        <TextField
          label="Label"
          value={name}
          autoFocus
          placeholder={chordDisplay(shortcut.chord)}
          hint="Leave it empty to show the shortcut itself."
          onChange={(value) => {
            setName(clampShortcutName(value));
          }}
        />
        <div className="button-row">
          <button type="submit" className="button button-primary" disabled={busy}>
            Save
          </button>
          <button type="button" className="button" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
