import { useId, useRef, useState, type KeyboardEvent, type Ref } from 'react';
import { acceptSuggestion, pathSeparator } from '../../lib/pathCompletion';
import { usePathSuggestions } from './usePathSuggestions';

interface PathFieldProps {
  label: string;
  /** The agent whose filesystem is browsed. */
  agentId: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  inputRef?: Ref<HTMLInputElement>;
  placeholder?: string;
  error?: string;
  /** Shown instead of the empty-field browse hint; a function words it around that hint. */
  hint?: string | ((browseHint: string) => string);
  autoFocus?: boolean;
  disabled?: boolean;
}

/** How to start browsing, worded for the agent host's path flavour. */
export function browseHint(windowsPaths: boolean): string {
  return windowsPaths
    ? 'Type C:\\ or ~ to browse folders on the agent.'
    : 'Type / or ~ to browse folders on the agent.';
}

/**
 * A host-path text field with folder suggestions: an ARIA 1.2 editable
 * combobox with list autocomplete. The list is rendered in flow under the
 * input rather than floating, so a dialog's scroll box never clips it.
 *
 * Picking a folder writes `<dir>/<name>/` and keeps the caret in the field, so
 * the next level's folders appear at once. Keys: ↓/↑ move through the list,
 * Enter or Tab take the highlighted folder (Tab only once one is highlighted,
 * so it still leaves the field otherwise), Escape — or Enter with nothing
 * highlighted, which also submits the form — closes the list until the text
 * changes.
 */
export function PathField({
  label,
  agentId,
  value,
  onChange,
  onBlur,
  inputRef,
  placeholder,
  error,
  hint,
  autoFocus,
  disabled,
}: PathFieldProps) {
  const id = useId();
  const listId = `${id}-list`;
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const [focused, setFocused] = useState(false);
  const [dismissedFor, setDismissedFor] = useState<string | null>(null);
  const [active, setActive] = useState(-1);
  const [activeFor, setActiveFor] = useState(value);
  const localRef = useRef<HTMLInputElement | null>(null);

  const suggestions = usePathSuggestions(agentId, value, focused && !disabled);
  const open = focused && dismissedFor !== value && suggestions.names.length > 0;
  // A highlight belongs to the text it was made for; typing clears it.
  const activeIndex = open && activeFor === value && active < suggestions.names.length ? active : -1;

  const browse = browseHint(suggestions.windowsPaths);
  const shownHint =
    typeof hint === 'function' ? hint(browse) : (hint ?? (value === '' ? browse : undefined));
  const separator = pathSeparator(suggestions.listedPath);
  const describedBy =
    error !== undefined ? errorId : shownHint !== undefined ? hintId : undefined;

  function pick(name: string) {
    onChange(acceptSuggestion(suggestions.listedPath, name));
    setActive(-1);
    localRef.current?.focus();
  }

  function move(delta: number) {
    const count = suggestions.names.length;
    const from = activeIndex < 0 ? (delta > 0 ? -1 : count) : activeIndex;
    setActive((from + delta + count) % count);
    setActiveFor(value);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!open) {
        if (dismissedFor === value && suggestions.names.length > 0) setDismissedFor(null);
        return;
      }
      event.preventDefault();
      move(event.key === 'ArrowDown' ? 1 : -1);
      return;
    }
    if (!open) return;
    if ((event.key === 'Enter' || event.key === 'Tab') && activeIndex >= 0) {
      event.preventDefault();
      pick(suggestions.names[activeIndex] ?? '');
      return;
    }
    if (event.key === 'Enter') {
      // The typed folder is the one wanted; the form still sees this Enter.
      setDismissedFor(value);
      return;
    }
    if (event.key === 'Escape') {
      // Also stops an enclosing <dialog> from treating this Escape as cancel.
      event.preventDefault();
      setDismissedFor(value);
      setActive(-1);
    }
  }

  return (
    <div className="field path-field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        ref={(node) => {
          localRef.current = node;
          if (typeof inputRef === 'function') inputRef(node);
          else if (inputRef !== undefined && inputRef !== null) inputRef.current = node;
        }}
        className="field-input"
        type="text"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        autoComplete="off"
        autoCapitalize="off"
        autoCorrect="off"
        spellCheck={false}
        placeholder={placeholder}
        autoFocus={autoFocus}
        disabled={disabled}
        aria-invalid={error !== undefined}
        aria-describedby={describedBy}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        onFocus={() => {
          setFocused(true);
        }}
        onBlur={() => {
          setFocused(false);
          onBlur?.();
        }}
        onKeyDown={onKeyDown}
      />
      <ul id={listId} role="listbox" aria-label={`${label} suggestions`} className="path-suggestions" hidden={!open}>
        {open &&
          suggestions.names.map((name, index) => (
            <li
              key={name}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === activeIndex}
              className="path-suggestion"
              // Keeps focus (and the soft keyboard) in the input.
              onMouseDown={(event) => {
                event.preventDefault();
              }}
              onClick={() => {
                pick(name);
              }}
            >
              {name + separator}
            </li>
          ))}
      </ul>
      {open && suggestions.overflow > 0 && (
        <p className="path-suggestions-more">+{suggestions.overflow} more — keep typing</p>
      )}
      {shownHint !== undefined && error === undefined && (
        <p className="field-hint" id={hintId}>
          {shownHint}
        </p>
      )}
      {error !== undefined && (
        <p className="field-error" id={errorId}>
          {error}
        </p>
      )}
    </div>
  );
}
