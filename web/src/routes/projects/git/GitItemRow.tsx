import { memo, type CSSProperties } from 'react';
import {
  canCheckOut,
  canRemoveWorktree,
  gitItemId,
  isDeletable,
  KIND_BRANCH_WITH_WORKTREE,
  type GitItem,
} from '../../../api/gitRepository';
import { chips, primaryLabel, secondaryLabel, tipLine, type GitFolderRow as Folder } from './gitDisplay';

interface GitItemRowProps {
  item: GitItem;
  /** The tree's leaf name; the full name stays in the accessible labels. */
  label: string;
  depth: number;
  selected: boolean;
  busy: boolean;
  /** `capabilities.git.branches`: without it the row has no Check out at all. */
  checkoutEnabled: boolean;
  /** `capabilities.git.delete_remote`: without it a remote row cannot be deleted. */
  deleteRemote: boolean;
  onToggle: (id: string, selected: boolean) => void;
  onDelete: (item: GitItem) => void;
  onRemoveWorktree: (item: GitItem) => void;
  onCheckout: (item: GitItem) => void;
}

function depthStyle(depth: number): CSSProperties {
  return { '--git-depth': depth } as CSSProperties;
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
  label,
  depth,
  selected,
  busy,
  checkoutEnabled,
  deleteRemote,
  onToggle,
  onDelete,
  onRemoveWorktree,
  onCheckout,
}: GitItemRowProps) {
  const deletable = isDeletable(item, deleteRemote);
  const checkoutable = checkoutEnabled && canCheckOut(item);
  const removable = canRemoveWorktree(item);
  const id = gitItemId(item);
  const secondary = secondaryLabel(item);
  const primary = primaryLabel(item);
  const tip = tipLine(item);
  const deleteText =
    item.kind === KIND_BRANCH_WITH_WORKTREE ? 'Delete branch and worktree' : 'Delete';

  return (
    <li className="git-row git-tree-row" style={depthStyle(depth)}>
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
        <span className="git-row-primary" title={primary}>
          {label}
        </span>
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

      {removable && (
        <button
          type="button"
          className="button"
          disabled={busy}
          aria-label={`Remove worktree of ${primary}`}
          onClick={() => {
            onRemoveWorktree(item);
          }}
        >
          Remove worktree
        </button>
      )}

      {deletable && (
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          aria-label={`${deleteText} ${primary}`}
          onClick={() => {
            onDelete(item);
          }}
        >
          {deleteText}
        </button>
      )}
    </li>
  );
});

interface GitFolderRowProps {
  folder: Folder;
  /** While a filter holds every matching folder open, a toggle would do nothing visible. */
  disabled: boolean;
  onToggle: (id: string) => void;
}

/** A branch-name folder: toggles its rows, never selectable or deletable itself. */
export function GitFolderRow({ folder, disabled, onToggle }: GitFolderRowProps) {
  return (
    <li className="git-folder-row git-tree-row" style={depthStyle(folder.depth)}>
      <button
        type="button"
        className="git-folder-toggle"
        aria-expanded={folder.expanded}
        disabled={disabled}
        onClick={() => {
          onToggle(folder.id);
        }}
      >
        <span className="git-folder-chevron" aria-hidden="true">
          {folder.expanded ? '▾' : '▸'}
        </span>
        <span className="git-folder-name">{folder.name}</span>
        <span className="git-folder-count">({folder.count})</span>
      </button>
    </li>
  );
}
