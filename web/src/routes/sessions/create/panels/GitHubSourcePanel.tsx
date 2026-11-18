import { useId, useState } from 'react';
import { TextField } from '../../../../components/form/TextField';
import type { GitHubSetupAction, GitHubStatus } from '../../../../api/autoscanRepository';
import { orderedUnion } from '../../../workspaces/workspaceForm';
import {
  GITHUB_LABELS_MAX,
  GITHUB_TEXT_MAX,
  githubLabelError,
  githubProblem,
} from '../autoScanSetup';
import type { AutoScanSourcesView } from '../useAutoScanSources';
import type { GitHubFilterDraft, WizardAction } from '../wizardState';

interface GitHubSourcePanelProps {
  filter: GitHubFilterDraft;
  sources: AutoScanSourcesView;
  submitting: boolean;
  dispatch: (action: WizardAction) => void;
}

/**
 * The GitHub half of the Auto Scan source step: whether `gh` on the PC can
 * read this workspace's repository (with buttons that open a terminal to
 * install it or sign in), and the GitHub filter.
 */
export function GitHubSourcePanel({ filter, sources, submitting, dispatch }: GitHubSourcePanelProps) {
  const headingId = useId();
  const datalistId = useId();
  const setFilter = (next: GitHubFilterDraft) => {
    dispatch({ type: 'setAutoScanGitHubFilter', filter: next });
  };
  const activeLabel = filter.activeLabel.trim();
  const activeLabelError = activeLabel.includes(',')
    ? 'A label name cannot contain a comma.'
    : activeLabel !== '' &&
        filter.labels.some((label) => label.toLowerCase() === activeLabel.toLowerCase())
      ? 'The label to add must not be one of the labels to pick up.'
      : activeLabel.length > GITHUB_TEXT_MAX
        ? `A label name can have at most ${String(GITHUB_TEXT_MAX)} characters.`
        : undefined;
  const repoLabels = sources.githubLabels.labels.map((label) => label.name);

  return (
    <section className="option-group" aria-labelledby={headingId}>
      <div className="option-group-head">
        <h3 id={headingId}>GitHub</h3>
        <button
          type="button"
          className="button"
          disabled={sources.githubChecking}
          onClick={sources.checkGitHub}
        >
          {sources.githubChecking ? 'Checking…' : 'Check again'}
        </button>
      </div>

      <div className="autoscan-github-status" role="status">
        {sources.github.kind === 'loading' ? (
          <p className="page-note state-loading">Checking GitHub on the PC…</p>
        ) : sources.github.kind === 'error' ? (
          <p className="field-error">Could not check GitHub on the PC: {sources.github.message}</p>
        ) : (
          <GitHubStatusLines
            status={sources.github.status}
            openSetup={sources.openGitHubSetup}
            busy={submitting || sources.githubSetupPending}
          />
        )}
      </div>
      {sources.githubSetupError !== null && (
        <p className="field-error">{sources.githubSetupError}</p>
      )}

      <GitHubLabelsField
        labels={filter.labels}
        repoLabels={repoLabels}
        status={sources.githubLabels.status}
        error={sources.githubLabels.error}
        datalistId={datalistId}
        onChange={(labels) => {
          setFilter({ ...filter, labels });
        }}
      />

      <TextField
        label="Assigned to"
        placeholder="Anyone"
        hint="Empty = any assignee. @me = the signed-in GitHub account."
        autoComplete="off"
        spellCheck={false}
        value={filter.assignee}
        onChange={(assignee) => {
          setFilter({ ...filter, assignee });
        }}
      />
      <TextField
        label="Label to add when work starts"
        hint="Optional, for example in-progress. It must exist in the repository."
        autoComplete="off"
        spellCheck={false}
        list={datalistId}
        error={activeLabelError}
        value={filter.activeLabel}
        onChange={(value) => {
          setFilter({ ...filter, activeLabel: value });
        }}
      />
      <datalist id={datalistId}>
        {repoLabels.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>

      <p className="field-hint">When an issue is closed, its worktree is removed.</p>
    </section>
  );
}

function GitHubStatusLines({
  status,
  openSetup,
  busy,
}: {
  status: GitHubStatus;
  openSetup: ((action: GitHubSetupAction) => void) | null;
  busy: boolean;
}) {
  const problem = githubProblem(status);

  if (!status.installed) {
    const installCommand = status.install_command ?? null;
    return (
      <>
        <p>GitHub CLI (gh) is not installed on the PC.</p>
        {status.can_install && openSetup !== null ? (
          <div className="autoscan-github-action">
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => {
                openSetup('install');
              }}
            >
              Install GitHub CLI
            </button>
            {installCommand !== null && (
              <p className="field-hint">
                Runs <code>{installCommand}</code> in a terminal on the PC.
              </p>
            )}
          </div>
        ) : (
          <p className="field-hint">Install it from cli.github.com, then check again.</p>
        )}
      </>
    );
  }

  // The agent names a host only where signing in can fix the problem.
  if (!status.signed_in && (status.host ?? '').includes('github')) {
    return (
      <>
        <p>{problem ?? 'GitHub CLI (gh) is not signed in on the PC.'}</p>
        {openSetup !== null ? (
          <div className="autoscan-github-action">
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => {
                openSetup('sign_in');
              }}
            >
              Sign in to GitHub
            </button>
          </div>
        ) : (
          <p className="field-hint">Run gh auth login on the PC, then check again.</p>
        )}
      </>
    );
  }

  if (problem !== null) return <p>{problem}</p>;

  const repo = status.repo ?? null;
  const repoUrl = status.repo_url ?? null;
  const account = status.account ?? null;
  return (
    <p>
      Repository{' '}
      {repo === null ? (
        'unknown'
      ) : repoUrl !== null ? (
        <a href={repoUrl} target="_blank" rel="noopener noreferrer">
          {repo}
        </a>
      ) : (
        repo
      )}
      {account !== null && ` · Signed in as ${account}`}
    </p>
  );
}

// A large repository has hundreds of labels: the unchosen ones are offered
// filtered by the typed text and capped, as on Android and iOS.
const MAX_LABEL_SUGGESTIONS = 30;

interface GitHubLabelsFieldProps {
  labels: readonly string[];
  repoLabels: readonly string[];
  status: AutoScanSourcesView['githubLabels']['status'];
  error: string | null;
  datalistId: string;
  onChange: (labels: string[]) => void;
}

/**
 * The trigger labels: the repository's labels as chips, plus a field for a
 * name the list does not show (labels could not be read yet, or the
 * repository has more than the agent returns). Chosen names the repository
 * does not list stay on as chips, so they can be unchecked.
 */
function GitHubLabelsField({
  labels,
  repoLabels,
  status,
  error,
  datalistId,
  onChange,
}: GitHubLabelsFieldProps) {
  const [draft, setDraft] = useState('');
  // A chip the user just toggled stays until they type again, so unchecking
  // one outside the shown suggestions does not make it (and focus) vanish.
  const [toggled, setToggled] = useState<ReadonlySet<string>>(() => new Set());
  const [draftError, setDraftError] = useState<string | undefined>(undefined);
  // Discovery first, then anything chosen it did not return — the Linear
  // section's order, so the chips do not jump when one is toggled.
  const options = orderedUnion(repoLabels, labels);
  const full = labels.length >= GITHUB_LABELS_MAX;
  const query = draft.trim().toLocaleLowerCase();
  const suggestions = repoLabels.filter(
    (name) => !labels.includes(name) && (query === '' || name.toLocaleLowerCase().includes(query)),
  );
  const shownSuggestions = new Set(suggestions.slice(0, MAX_LABEL_SUGGESTIONS));
  const hiddenCount = suggestions.length - shownSuggestions.size;
  const visible = options.filter(
    (name) => labels.includes(name) || shownSuggestions.has(name) || toggled.has(name),
  );

  const toggle = (name: string) => {
    setToggled((current) => new Set(current).add(name));
    const next = new Set(labels);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange(options.filter((option) => next.has(option)));
  };

  const add = () => {
    const message = githubLabelError(draft, labels);
    if (message !== null) {
      setDraftError(message);
      return;
    }
    onChange([...labels, draft.trim()]);
    setDraft('');
    setDraftError(undefined);
  };

  return (
    <fieldset className="chip-group">
      <legend>Labels to pick up</legend>
      <p className="field-hint">
        An open issue with any of these labels is picked up. The scan removes the label when
        it starts, so each issue runs once.
      </p>
      {status === 'loading' && (
        <p className="field-hint state-loading">Loading the repository’s labels…</p>
      )}
      {status === 'error' && (
        <p className="field-hint">
          Couldn’t load the repository’s labels: {error}. You can still type label names.
        </p>
      )}
      {visible.length > 0 && (
        <div className="chip-row">
          {visible.map((name) => {
            const checked = labels.includes(name);
            return (
              <label key={name} className="chip">
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!checked && full}
                  onChange={() => {
                    toggle(name);
                  }}
                />
                {name}
              </label>
            );
          })}
        </div>
      )}
      {hiddenCount > 0 && (
        <p className="field-hint">
          {hiddenCount} more {hiddenCount === 1 ? 'label' : 'labels'}: type to find them.
        </p>
      )}
      {full && (
        <p className="field-hint">
          You can pick up at most {GITHUB_LABELS_MAX} labels.
        </p>
      )}
      <form
        className="autoscan-label-add"
        onSubmit={(event) => {
          event.preventDefault();
          add();
        }}
      >
        <TextField
          label="Add a label"
          autoComplete="off"
          spellCheck={false}
          list={datalistId}
          error={draftError}
          value={draft}
          onChange={(value) => {
            setDraft(value);
            setDraftError(undefined);
            setToggled(new Set());
          }}
        />
        <button type="submit" className="button" disabled={draft.trim() === '' || full}>
          Add
        </button>
      </form>
    </fieldset>
  );
}
