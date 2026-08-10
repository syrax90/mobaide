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
  dispatch: (action: WizardAction) => void;
  onContinue: () => void;
}

export function ProjectStep({
  projects,
  selection,
  customPathDraft,
  customPathError,
  recentPaths,
  onForgetRecent,
  dispatch,
  onContinue,
}: ProjectStepProps) {
  // Mirrors what Continue will actually send. A non-empty draft wins over a
  // stored project there, so the gate has to follow the same precedence —
  // otherwise picking a project and then mistyping a path leaves an enabled
  // button that silently discards the project.
  const draft = customPathDraft.trim();
  const canContinue = draft !== '' ? customPathError === null : selection.kind === 'stored';

  return (
    <div className="wizard-body">
      <h2>Where should it run?</h2>

      {projects.length === 0 ? (
        <p className="empty-state">
          This agent has no projects yet. Add one from the mobile app, or enter a path below.
        </p>
      ) : (
        <ul className="project-list">
          {projects.map((project) => {
            const active =
              draft === '' && selection.kind === 'stored' && selection.name === project.name;
            return (
              <li key={project.name}>
                <button
                  type="button"
                  className={`project-row${active ? ' project-row-selected' : ''}`}
                  aria-pressed={active}
                  onClick={() => {
                    dispatch({ type: 'selectStoredProject', project });
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
        </ul>
      )}

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
          types, which is both mobile clients' behaviour. Picking one dispatches
          the same action a keystroke does, so the validation, the resulting
          selection and the Continue gate below are shared with typed text.
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
                      dispatch({ type: 'setCustomPathDraft', draft: path });
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
