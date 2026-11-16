import type { WorkspaceInfo } from '../../api/workspaceRepository';

interface WorkspaceRowProps {
  workspace: WorkspaceInfo;
  index: number;
  total: number;
  busy: boolean;
  onEdit: (workspace: WorkspaceInfo) => void;
  onDelete: (workspace: WorkspaceInfo) => void;
  onOpenGit: (workspace: WorkspaceInfo) => void;
  onMove: (from: number, to: number) => void;
  onDragStart: (index: number) => void;
  onDropOn: (index: number) => void;
}

/**
 * One workspace card.
 *
 * Reorder is offered twice on purpose. Move up / Move down are the real
 * mechanism — they work with a mouse, a touchscreen, a keyboard and a screen
 * reader alike — while the drag handle is a desktop-mouse convenience built on
 * the HTML5 drag API, which most mobile browsers do not implement. Mobile's
 * long-press drag has no honest browser equivalent, so the buttons lead.
 */
export function WorkspaceRow({
  workspace,
  index,
  total,
  busy,
  onEdit,
  onDelete,
  onOpenGit,
  onMove,
  onDragStart,
  onDropOn,
}: WorkspaceRowProps) {
  const conda = workspace.conda_env ?? '';

  return (
    <li
      className="workspace-card"
      onDragOver={(event) => {
        // Without this the drop is refused and `onDrop` never fires.
        event.preventDefault();
      }}
      onDrop={(event) => {
        event.preventDefault();
        onDropOn(index);
      }}
    >
      <span
        className="workspace-drag-handle"
        draggable
        aria-hidden="true"
        onDragStart={() => {
          onDragStart(index);
        }}
      >
        ⠿
      </span>

      <button
        type="button"
        className="workspace-card-main"
        onClick={() => {
          onEdit(workspace);
        }}
      >
        <span className="workspace-name">{workspace.name}</span>
        <span className="workspace-path">
          {!workspace.path_exists && (
            <span className="workspace-path-warning" aria-label="Path does not exist">
              ⚠
            </span>
          )}
          {workspace.path}
        </span>
        <span className="workspace-badges">
          {workspace.is_git && <span className="workspace-badge">git</span>}
          {workspace.has_linear_key && <span className="workspace-badge">Linear key</span>}
          {conda !== '' && <span className="workspace-badge">conda:{conda}</span>}
        </span>
      </button>

      <div className="workspace-card-actions">
        <button
          type="button"
          className="button button-icon"
          disabled={busy || index === 0}
          aria-label={`Move ${workspace.name} up`}
          onClick={() => {
            onMove(index, index - 1);
          }}
        >
          ↑
        </button>
        <button
          type="button"
          className="button button-icon"
          disabled={busy || index === total - 1}
          aria-label={`Move ${workspace.name} down`}
          onClick={() => {
            onMove(index, index + 1);
          }}
        >
          ↓
        </button>
        {workspace.is_git && (
          <button
            type="button"
            className="button"
            onClick={() => {
              onOpenGit(workspace);
            }}
          >
            Git
          </button>
        )}
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            onDelete(workspace);
          }}
        >
          Delete
        </button>
      </div>
    </li>
  );
}
