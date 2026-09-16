import { memo } from 'react';
import { canCheckOut, gitItemId, isDeletable, type GitItem } from '../../../api/gitRepository';
import { chips, primaryLabel, secondaryLabel, tipLine } from './gitDisplay';

interface GitItemRowProps {
  item: GitItem;
  selected: boolean;
  busy: boolean;
  /** `capabilities.git.branches`: without it the row has no Check out at all. */
  checkoutEnabled: boolean;
  onToggle: (id: string, selected: boolean) => void;
  onDelete: (item: GitItem) => void;
  onCheckout: (item: GitItem) => void;
}

/**
 * Memoised because the row count is unbounded in practice — auto-scan creates a
 * branch and a worktree per Linear issue — and without it every checkbox tap
 * rebuilds all of them. React Query's structural sharing keeps `item` identity
 * stable across refetches, so the comparison actually pays off; the callbacks
 * are stabilised on the `GitScreen` side.
 */
export const GitItemRow = memo(function GitItemRow({
  item,
  selected,
  busy,
  checkoutEnabled,
  onToggle,
  onDelete,
  onCheckout,
}: GitItemRowProps) {
  const deletable = isDeletable(item);
  const checkoutable = checkoutEnabled && canCheckOut(item);
  const id = gitItemId(item);
  const secondary = secondaryLabel(item);
  const primary = primaryLabel(item);
  const tip = tipLine(item);

  return (
    <li className="git-row">
      {/* The checked-out branch has no checkbox at all rather than a disabled
          one: it can never be deleted, so an unusable control is just noise. */}
      {deletable && (
        <input
          type="checkbox"
          checked={selected}
          disabled={busy}
          aria-label={`Select ${primary}`}
          onChange={(event) => {
            onToggle(id, event.target.checked);
          }}
        />
      )}

      <div className="git-row-text">
        <span className="git-row-primary">{primary}</span>
        {secondary !== null && <span className="git-row-secondary">{secondary}</span>}
        {tip !== null && <span className="git-row-secondary">{tip}</span>}
        <span className="git-row-chips">
          {chips(item).map((chip) => (
            <span key={chip.text} className={`git-chip git-chip-${chip.style}`}>
              {chip.text}
            </span>
          ))}
        </span>
      </div>

      {checkoutable && (
        <button
          type="button"
          className="button"
          disabled={busy}
          aria-label={`Check out ${primary}`}
          onClick={() => {
            onCheckout(item);
          }}
        >
          Check out
        </button>
      )}

      {deletable && (
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          aria-label={`Delete ${primary}`}
          onClick={() => {
            onDelete(item);
          }}
        >
          Delete
        </button>
      )}
    </li>
  );
});
