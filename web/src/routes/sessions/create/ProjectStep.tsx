import type { ProjectSummary } from '../../../api/projectRepository';
import { TextField } from '../../../components/form/TextField';
import type { ProjectSelection, WizardAction } from './wizardState';

interface ProjectStepProps {
  projects: readonly ProjectSummary[];
  selection: ProjectSelection;
  customPathDraft: string;
  customPathError: string | null;
  dispatch: (action: WizardAction) => void;
  onContinue: () => void;
}

export function ProjectStep({
  projects,
  selection,
  customPathDraft,
  customPathError,
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
                    {project.isGit && <span className="project-badge">git</span>}
                    {project.hasLinearKey && <span className="project-badge">linear</span>}
                    {!project.pathExists && (
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
