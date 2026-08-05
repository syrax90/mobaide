import type { ProjectInfo } from '../../api/projectRepository';

interface ProjectRowProps {
  project: ProjectInfo;
  index: number;
  total: number;
  busy: boolean;
  onEdit: (project: ProjectInfo) => void;
  onDelete: (project: ProjectInfo) => void;
  onOpenGit: (project: ProjectInfo) => void;
  onMove: (from: number, to: number) => void;
  onDragStart: (index: number) => void;
  onDropOn: (index: number) => void;
}

/**
 * One project card.
 *
 * Reorder is offered twice on purpose. Move up / Move down are the real
 * mechanism — they work with a mouse, a touchscreen, a keyboard and a screen
 * reader alike — while the drag handle is a desktop-mouse convenience built on
 * the HTML5 drag API, which most mobile browsers do not implement. Mobile's
 * long-press drag has no honest browser equivalent, so the buttons lead.
 */
export function ProjectRow({
  project,
  index,
  total,
  busy,
  onEdit,
  onDelete,
  onOpenGit,
  onMove,
  onDragStart,
  onDropOn,
}: ProjectRowProps) {
  const conda = project.conda_env ?? '';

  return (
    <li
      className="project-card"
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
        className="project-drag-handle"
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
        className="project-card-main"
        onClick={() => {
          onEdit(project);
        }}
      >
        <span className="project-name">{project.name}</span>
        <span className="project-path">
          {!project.path_exists && (
            <span className="project-path-warning" aria-label="Path does not exist">
              ⚠
            </span>
          )}
          {project.path}
        </span>
        <span className="project-badges">
          {project.is_git && <span className="project-badge">git</span>}
          {project.has_linear_key && <span className="project-badge">Linear key</span>}
          {conda !== '' && <span className="project-badge">conda:{conda}</span>}
        </span>
      </button>

      <div className="project-card-actions">
        <button
          type="button"
          className="button button-icon"
          disabled={busy || index === 0}
          aria-label={`Move ${project.name} up`}
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
          aria-label={`Move ${project.name} down`}
          onClick={() => {
            onMove(index, index + 1);
          }}
        >
          ↓
        </button>
        {project.is_git && (
          <button
            type="button"
            className="button"
            onClick={() => {
              onOpenGit(project);
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
            onDelete(project);
          }}
        >
          Delete
        </button>
      </div>
    </li>
  );
}
