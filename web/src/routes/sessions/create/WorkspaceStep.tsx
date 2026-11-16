import type { WorkspaceSummary } from '../../../api/workspaceRepository';
import { PathField } from '../../../components/form/PathField';
import { useDragReorder } from '../../../lib/useDragReorder';
import { DragHandle, REORDER_HINT } from './DragHandle';
import type { WorkspaceSelection, WizardAction } from './wizardState';

interface WorkspaceStepProps {
  /** The agent whose folders the custom-path field suggests. */
  agentId: string;
  workspaces: readonly WorkspaceSummary[];
  selection: WorkspaceSelection;
  customPathDraft: string;
  customPathError: string | null;
  /** Previously launched one-off directories for this agent, most-recent-first. */
  recentPaths: readonly string[];
  onForgetRecent: (path: string) => void;
  /** Picking a row is the whole act — the container records it and advances. */
  onSelectWorkspace: (workspace: WorkspaceSummary) => void;
  /** As above for a recent directory; the container re-validates before it moves. */
  onSelectRecentPath: (path: string) => void;
  /** Opens the workspace form; the container selects the saved workspace and advances. */
  onCreateWorkspace: () => void;
  /** Move a workspace row; the container saves the agent's new workspace order. */
  onMoveWorkspace: (from: number, to: number) => void;
  /** The one-off path field only — typing must not reach the URL (see `NewSession`). */
  dispatch: (action: WizardAction) => void;
  onContinue: () => void;
}

/**
 * One click on a workspace row — or on a recent directory — both records the
 * choice and advances, as `ModeStep` does. Continue survives for the single case
 * with no row to click: a path typed into the field.
 *
 * A deliberate divergence from the list + Continue that Android and iOS
 * keep; on web the URL already records the selection, so
 * the confirm click adds a history entry carrying nothing the row click has not
 * decided. Mirrors tracked as #650 (Android) and #651 (iOS).
 *
 * "Create new workspace…" is the last row even when there are no workspaces, as in
 * both mobile pickers. Saving a new workspace counts as picking it, so it
 * advances too.
 *
 * Each workspace row has a drag handle. The order it saves is the agent's own
 * workspace order (`PATCH /api/projects/order`), so every client shows it.
 */
export function WorkspaceStep({
  agentId,
  workspaces,
  selection,
  customPathDraft,
  customPathError,
  recentPaths,
  onForgetRecent,
  onSelectWorkspace,
  onSelectRecentPath,
  onCreateWorkspace,
  onMoveWorkspace,
  dispatch,
  onContinue,
}: WorkspaceStepProps) {
  const sortable = useDragReorder({
    keys: workspaces.map((workspace) => workspace.name),
    onMove: onMoveWorkspace,
  });
  const reorderable = workspaces.length > 1;

  // The button exists for the one choice with no row to click. Both terms are
  // load-bearing: `customPathError` is `null` on a step nobody has typed into
  // (`INITIAL_WIZARD_STATE`, and the `syncCustomPathFromUrl` clear), so the
  // emptiness test is the whole of what keeps a fresh step from reading as valid.
  const draft = customPathDraft.trim();
  const canContinue = draft !== '' && customPathError === null;

  return (
    <div className="wizard-body">
      <h2>Where should it run?</h2>
      {reorderable && <p className="field-hint">{REORDER_HINT}</p>}

      {workspaces.length === 0 && (
        <p className="empty-state">
          This agent has no workspaces yet. Create one, or enter a path below.
        </p>
      )}

      <ul className="workspace-list" {...sortable.listProps}>
        {workspaces.map((workspace, index) => {
          // Only ever seen on a step that was arrived at — the last-launch
          // pre-fill, the wizard's own Back, a deep link — since a click
          // leaves. `draft === ''` still earns its place: while a path is
          // being typed, the highlighted row is not what Continue would send.
          const active =
            draft === '' && selection.kind === 'stored' && selection.name === workspace.name;
          return (
            <li key={workspace.name} className="sortable-item" {...sortable.itemProps(index)}>
              {reorderable && (
                <DragHandle label={workspace.name} {...sortable.handleProps(index)} />
              )}
              <button
                type="button"
                className={`workspace-row${active ? ' workspace-row-selected' : ''}`}
                aria-pressed={active}
                onClick={() => {
                  onSelectWorkspace(workspace);
                }}
              >
                <span className="workspace-name">{workspace.name}</span>
                <span className="workspace-path">{workspace.path}</span>
                <span className="workspace-badges">
                  {workspace.is_git && <span className="workspace-badge">git</span>}
                  {workspace.has_linear_key && <span className="workspace-badge">linear</span>}
                  {!workspace.path_exists && (
                    <span className="workspace-badge workspace-badge-warning">missing</span>
                  )}
                </span>
              </button>
            </li>
          );
        })}

        <li className="sortable-item">
          {reorderable && <span className="drag-handle-spacer" aria-hidden="true" />}
          <button type="button" className="workspace-row" onClick={onCreateWorkspace}>
            <span className="workspace-name">Create new workspace…</span>
            <span className="workspace-path">Name + path, saved on this agent for next time</span>
          </button>
        </li>
      </ul>

      <div className="option-group">
        <h3>Or a one-off directory</h3>
        <PathField
          label="Absolute path"
          agentId={agentId}
          value={customPathDraft}
          placeholder="/Users/me/code/workspace"
          error={customPathError ?? undefined}
          onChange={(draft) => {
            dispatch({ type: 'setCustomPathDraft', draft });
          }}
        />

        {/*
          Never filtered against the draft — all five stay up while the user
          types, which is both mobile clients' behaviour. Picking one is a
          complete act: it fills the field and advances, like a workspace row. The
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
