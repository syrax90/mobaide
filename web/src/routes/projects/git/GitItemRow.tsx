import { memo } from 'react';
import { gitItemId, isDeletable, type GitItem } from '../../../api/gitRepository';
import { chips, primaryLabel, secondaryLabel } from './gitDisplay';

interface GitItemRowProps {
  item: GitItem;
  selected: boolean;
  busy: boolean;
  onToggle: (id: string, selected: boolean) => void;
  onDelete: (item: GitItem) => void;
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
  onToggle,
  onDelete,
}: GitItemRowProps) {
  const deletable = isDeletable(item);
  const id = gitItemId(item);
  const secondary = secondaryLabel(item);
  const primary = primaryLabel(item);

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
        <span className="git-row-chips">
          {chips(item).map((chip) => (
            <span key={chip.text} className={`git-chip git-chip-${chip.style}`}>
              {chip.text}
            </span>
          ))}
        </span>
      </div>

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
