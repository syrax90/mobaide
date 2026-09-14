import type { ProjectSummary } from '../../../api/projectRepository';
import { TextField } from '../../../components/form/TextField';
import type { ProjectSelection, WizardAction } from './wizardState';

interface ProjectStepProps {
  projects: readonly ProjectSummary[];
  selection: ProjectSelection;
  customPathDraft: string;
  customPathError: string | null;
  /** Previously launched one-off directories for this agent, most-recent-first. */
  recentPaths: readonly string[];
  onForgetRecent: (path: string) => void;
  /** Picking a row is the whole act — the container records it and advances. */
  onSelectProject: (project: ProjectSummary) => void;
  /** As above for a recent directory; the container re-validates before it moves. */
  onSelectRecentPath: (path: string) => void;
  /** Opens the project form; the container selects the saved project and advances. */
  onCreateProject: () => void;
  /** The one-off path field only — typing must not reach the URL (see `NewSession`). */
  dispatch: (action: WizardAction) => void;
  onContinue: () => void;
}

/**
 * One click on a project row — or on a recent directory — both records the
 * choice and advances, as `ModeStep` does. Continue survives for the single case
 * with no row to click: a path typed into the field.
 *
 * A deliberate divergence from Android's dropdown + Continue and iOS's list +
 * Continue, which keep theirs; on web the URL already records the selection, so
 * the confirm click adds a history entry carrying nothing the row click has not
 * decided. Mirrors tracked as #650 (Android) and #651 (iOS).
 *
 * "Create new project…" is the last row even when there are no projects, as in
 * both mobile pickers. Saving a new project counts as picking it, so it
 * advances too.
 */
export function ProjectStep({
  projects,
  selection,
  customPathDraft,
  customPathError,
  recentPaths,
  onForgetRecent,
  onSelectProject,
  onSelectRecentPath,
  onCreateProject,
  dispatch,
  onContinue,
}: ProjectStepProps) {
  // The button exists for the one choice with no row to click. Both terms are
  // load-bearing: `customPathError` is `null` on a step nobody has typed into
  // (`INITIAL_WIZARD_STATE`, and the `syncCustomPathFromUrl` clear), so the
  // emptiness test is the whole of what keeps a fresh step from reading as valid.
  const draft = customPathDraft.trim();
  const canContinue = draft !== '' && customPathError === null;

  return (
    <div className="wizard-body">
      <h2>Where should it run?</h2>

      {projects.length === 0 && (
        <p className="empty-state">
          This agent has no projects yet. Create one, or enter a path below.
        </p>
      )}

      <ul className="project-list">
        {projects.map((project) => {
          // Only ever seen on a step that was arrived at — the last-launch
          // pre-fill, the wizard's own Back, a deep link — since a click
          // leaves. `draft === ''` still earns its place: while a path is
          // being typed, the highlighted row is not what Continue would send.
          const active =
            draft === '' && selection.kind === 'stored' && selection.name === project.name;
          return (
            <li key={project.name}>
              <button
                type="button"
                className={`project-row${active ? ' project-row-selected' : ''}`}
                aria-pressed={active}
                onClick={() => {
                  onSelectProject(project);
                }}
              >
                <span className="project-name">{project.name}</span>
                <span className="project-path">{project.path}</span>
                <span className="project-badges">
                  {project.is_git && <span className="project-badge">git</span>}
                  {project.has_linear_key && <span className="project-badge">linear</span>}
                  {!project.path_exists && (
                    <span className="project-badge project-badge-warning">missing</span>
                  )}
                </span>
              </button>
            </li>
          );
        })}

        <li>
          <button type="button" className="project-row" onClick={onCreateProject}>
            <span className="project-name">Create new project…</span>
            <span className="project-path">Name + path, saved on this agent for next time</span>
          </button>
        </li>
      </ul>

      <div className="option-group">
        <h3>Or a one-off directory</h3>
        <TextField
          label="Absolute path"
          value={customPathDraft}
          placeholder="/Users/me/code/project"
          error={customPathError ?? undefined}
          onChange={(draft) => {
            dispatch({ type: 'setCustomPathDraft', draft });
          }}
        />

        {/*
          Never filtered against the draft — all five stay up while the user
          types, which is both mobile clients' behaviour. Picking one is a
          complete act: it fills the field and advances, like a project row. The
          container re-validates first, so a hand-edited storage blob holding a
          relative path lands back here under the inline error instead of a URL
          the clamp would bounce.
        */}
        {recentPaths.length > 0 && (
          <div className="recent-paths">
            <h4>Recent paths</h4>
            <ul className="recent-path-list">
              {recentPaths.map((path) => (
                <li key={path} className="recent-path-row">
                  <button
                    type="button"
                    className="recent-path-pick"
                    title={path}
                    onClick={() => {
                      onSelectRecentPath(path);
                    }}
                  >
                    {path}
                  </button>
                  <button
                    type="button"
                    className="button button-icon"
                    aria-label={`Remove ${path}`}
                    onClick={() => {
                      onForgetRecent(path);
                    }}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="wizard-actions">
        <button
          type="button"
          className="button button-primary"
          disabled={!canContinue}
          onClick={onContinue}
        >
          Continue
        </button>
      </div>
    </div>
  );
}
