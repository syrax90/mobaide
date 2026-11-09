import {
  useEffect,
  useId,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
} from 'react';
import type { WorktreeBases } from '../../../../api/gitRepository';
import { commitAge, filterWorktreeBases } from '../worktreeBase';

export type BaseBranchStatus = 'loading' | 'error' | 'ready';

export interface BaseBranchPickerProps {
  status: BaseBranchStatus;
  bases: WorktreeBases | undefined;
  /** `null` is "Default branch": the agent picks its own start point. */
  value: string | null;
  onChange: (ref: string | null) => void;
  onRetry: () => void;
}

/** Repositories can carry hundreds of branches; the filter narrows past this. */
const MAX_ROWS = 200;

interface Row {
  ref: string | null;
  isDefault: boolean;
  isLocal: boolean;
  age: string;
  subject: string | null;
}

/**
 * The New Session base-branch picker: a button that opens an in-flow panel
 * with a filter input (an ARIA 1.2 combobox) over a listbox of refs. In flow
 * rather than floating, like `PathField`'s suggestions, so no scroll box clips
 * it. Keys in the filter: ↓/↑ move, Enter picks, Escape closes back to the
 * button; leaving the picker with Tab or a click closes it too.
 */
export function BaseBranchPicker({ status, bases, value, onChange, onRetry }: BaseBranchPickerProps) {
  const id = useId();
  const labelId = `${id}-label`;
  const triggerId = `${id}-trigger`;
  const listId = `${id}-list`;
  const hintId = `${id}-hint`;
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const filterRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);

  const ready = status === 'ready' && bases !== undefined;
  const isOpen = open && ready;
  const now = Date.now();

  const filtered = ready ? filterWorktreeBases(bases.refs, query) : [];
  const rows: Row[] = [
    // The agent-default row only while unfiltered: it matches no ref name.
    ...(query.trim() === ''
      ? [{ ref: null, isDefault: false, isLocal: false, age: '', subject: null }]
      : []),
    ...filtered.slice(0, MAX_ROWS).map((entry) => ({
      ref: entry.ref,
      isDefault: entry.ref === bases?.default,
      isLocal: entry.kind === 'local',
      age: commitAge(entry.committed_at, now),
      subject: entry.subject ?? null,
    })),
  ];
  const overflow = Math.max(0, filtered.length - MAX_ROWS);
  const activeIndex = rows.length === 0 ? -1 : Math.min(active, rows.length - 1);

  useEffect(() => {
    if (isOpen) filterRef.current?.focus();
  }, [isOpen]);

  const openPicker = (): void => {
    if (!ready) return;
    setQuery('');
    const current = value === null ? 0 : bases.refs.findIndex((entry) => entry.ref === value) + 1;
    setActive(Math.max(0, current));
    setOpen(true);
  };

  const closeToTrigger = (): void => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const pick = (ref: string | null): void => {
    onChange(ref);
    closeToTrigger();
  };

  const onFilterKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    switch (event.key) {
      case 'ArrowDown':
      case 'ArrowUp': {
        event.preventDefault();
        if (rows.length === 0) return;
        const delta = event.key === 'ArrowDown' ? 1 : -1;
        setActive((activeIndex + delta + rows.length) % rows.length);
        return;
      }
      case 'Enter': {
        // Never submits the wizard from inside the picker.
        event.preventDefault();
        const row = rows[activeIndex];
        if (row !== undefined) pick(row.ref);
        return;
      }
      case 'Escape':
        event.preventDefault();
        closeToTrigger();
        return;
      default:
        return;
    }
  };

  const onBlur = (event: FocusEvent<HTMLDivElement>): void => {
    if (!isOpen) return;
    const next = event.relatedTarget;
    if (next instanceof Node && containerRef.current?.contains(next)) return;
    setOpen(false);
  };

  const triggerText =
    status === 'loading' ? 'Loading branches…' : (value ?? 'Default branch');

  return (
    <div className="field base-branch" ref={containerRef} onBlur={onBlur}>
      <span className="field-label" id={labelId}>
        Base branch
      </span>
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        className="field-input base-branch-trigger"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={isOpen ? listId : undefined}
        aria-labelledby={`${labelId} ${triggerId}`}
        aria-describedby={hintId}
        disabled={!ready}
        onClick={() => {
          if (isOpen) setOpen(false);
          else openPicker();
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' && !isOpen) {
            event.preventDefault();
            openPicker();
          }
        }}
      >
        {triggerText}
      </button>
      {isOpen && (
        <div className="base-branch-panel">
          <input
            ref={filterRef}
            className="field-input"
            type="text"
            role="combobox"
            aria-label="Filter branches"
            aria-autocomplete="list"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            placeholder="Filter branches"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onFilterKeyDown}
          />
          <ul id={listId} role="listbox" aria-labelledby={labelId} className="path-suggestions base-branch-list">
            {rows.map((row, index) => (
              <li
                key={row.ref ?? ''}
                id={`${listId}-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                className="path-suggestion base-branch-option"
                title={row.subject ?? undefined}
                // Keeps focus in the filter input.
                onMouseDown={(event) => {
                  event.preventDefault();
                }}
                onClick={() => {
                  pick(row.ref);
                }}
              >
                <span className="base-branch-check" aria-hidden="true">
                  {row.ref === value ? '✓' : ''}
                </span>
                <span className="base-branch-name">{row.ref ?? 'Default branch'}</span>
                {row.isDefault && <span className="project-badge base-branch-badge-default">default</span>}
                {row.isLocal && <span className="project-badge">local</span>}
                {row.age !== '' && <span className="base-branch-age">{row.age}</span>}
              </li>
            ))}
          </ul>
          {rows.length === 0 && <p className="path-suggestions-more">No branches match.</p>}
          {overflow > 0 && (
            <p className="path-suggestions-more">+{overflow} more — keep typing</p>
          )}
        </div>
      )}
      {status === 'error' ? (
        <div className="base-branch-error" id={hintId}>
          <p className="field-error">Couldn’t load branches</p>
          <button type="button" className="button" onClick={onRetry}>
            Retry
          </button>
        </div>
      ) : (
        <p className="field-hint" id={hintId}>
          The branch the new worktree starts from.
        </p>
      )}
    </div>
  );
}
