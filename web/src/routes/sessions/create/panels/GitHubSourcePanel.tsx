import { useId, useState, type ReactNode } from 'react';
import { TextField } from '../../../../components/form/TextField';
import type {
  GitHubIssueType,
  GitHubSetupAction,
  GitHubStatus,
} from '../../../../api/autoscanRepository';
import { orderedUnion } from '../../../workspaces/workspaceForm';
import {
  GITHUB_ISSUE_TYPES_MAX,
  GITHUB_LABELS_MAX,
  GITHUB_TEXT_MAX,
  githubProblem,
} from '../autoScanSetup';
import type { AutoScanSourcesView } from '../useAutoScanSources';
import type { GitHubFilterDraft, WizardAction } from '../wizardState';
import { ScanCallout } from './ScanCallout';

interface GitHubSourcePanelProps {
  filter: GitHubFilterDraft;
  sources: AutoScanSourcesView;
  submitting: boolean;
  dispatch: (action: WizardAction) => void;
}

/**
 * The GitHub half of "Which issues": whether `gh` on the PC can read this
 * workspace's repository (with buttons that open a terminal to install it or
 * sign in), how the labels pick issues up, and the GitHub filter.
 */
export function GitHubSourcePanel({ filter, sources, submitting, dispatch }: GitHubSourcePanelProps) {
  const datalistId = useId();
  const tipsId = useId();
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
    <>
      <div className="autoscan-github-head">
        <div className="autoscan-github-status" role="status">
          {sources.github.kind === 'loading' ? (
            <p className="page-note state-loading">Checking GitHub on the PC…</p>
          ) : sources.github.kind === 'error' ? (
            <StatusLine tone="warning">
              Could not check GitHub on the PC: {sources.github.message}
            </StatusLine>
          ) : (
            <GitHubStatusLines
              status={sources.github.status}
              openSetup={sources.openGitHubSetup}
              busy={submitting || sources.githubSetupPending}
            />
          )}
        </div>
        <button
          type="button"
          className="button"
          disabled={sources.githubChecking}
          onClick={sources.checkGitHub}
        >
          {sources.githubChecking ? 'Checking…' : 'Check again'}
        </button>
      </div>
      {sources.githubSetupError !== null && (
        <p className="field-error">{sources.githubSetupError}</p>
      )}

      <ScanCallout tone="important" title="How issues are picked up">
        <p>
          An open issue with any of these labels is picked up. When work starts, the scan
          removes the label, so each issue runs only once. Add the label again to run the
          issue again.
        </p>
      </ScanCallout>
      <div className="autoscan-tip">
        <p className="autoscan-tip-title" id={tipsId}>
          Tips
        </p>
        <ul aria-labelledby={tipsId}>
          <li>
            Create a dedicated label, for example <code>auto-scan</code>, and add it only to the
            issues an agent should solve.
          </li>
          <li>
            Avoid common labels such as <code>bug</code>: every open issue with them would start
            a session.
          </li>
          <li>
            Keep the label out of issue templates and workflows that label new issues, or anyone
            who opens an issue could start a session on your PC.
          </li>
          <li>
            Use one label per kind of work, such as <code>auto-scan-small</code> and{' '}
            <code>auto-scan-complex</code>, and save a template for each.
          </li>
        </ul>
      </div>

      <GitHubLabelsField
        labels={filter.labels}
        repoLabels={repoLabels}
        status={sources.githubLabels.status}
        error={sources.githubLabels.error}
        onRetry={sources.retryGitHubLabels}
        onChange={(labels) => {
          setFilter({ ...filter, labels });
        }}
      />

      <GitHubIssueTypesField
        chosen={filter.issueTypes}
        repoTypes={sources.githubLabels.issueTypes}
        status={sources.githubLabels.status}
        onChange={(issueTypes) => {
          setFilter({ ...filter, issueTypes });
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
    </>
  );
}

/** One status sentence behind a ✓ or ⚠ mark; the mark is decoration, the words carry it. */
function StatusLine({ tone, children }: { tone: 'ok' | 'warning'; children: ReactNode }) {
  return (
    <p className={`autoscan-status autoscan-status-${tone}`}>
      <span className="autoscan-status-mark" aria-hidden="true">
        {tone === 'ok' ? '✓' : '!'}
      </span>
      <span>{children}</span>
    </p>
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
        <StatusLine tone="warning">GitHub CLI (gh) is not installed on the PC.</StatusLine>
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
        <StatusLine tone="warning">
          {problem ?? 'GitHub CLI (gh) is not signed in on the PC.'}
        </StatusLine>
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

  if (problem !== null) return <StatusLine tone="warning">{problem}</StatusLine>;

  const repo = status.repo ?? null;
  const repoUrl = status.repo_url ?? null;
  const account = status.account ?? null;
  return (
    <StatusLine tone="ok">
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
    </StatusLine>
  );
}

// A large repository has hundreds of labels: past this many, a search field
// narrows the unchosen ones, as on Android and iOS.
const MAX_LABEL_SUGGESTIONS = 30;

interface GitHubLabelsFieldProps {
  labels: readonly string[];
  repoLabels: readonly string[];
  status: AutoScanSourcesView['githubLabels']['status'];
  error: string | null;
  onRetry: () => void;
  onChange: (labels: string[]) => void;
}

/**
 * The trigger labels, as chips of the repository's labels. Only the
 * repository's labels can be chosen; the search field only narrows them.
 * Chosen names the repository does not list (from a template, or removed
 * since) stay on as chips, so they can be unchecked.
 */
function GitHubLabelsField({
  labels,
  repoLabels,
  status,
  error,
  onRetry,
  onChange,
}: GitHubLabelsFieldProps) {
  const [query, setQuery] = useState('');
  // A chip the user just toggled stays until they search again, so unchecking
  // one outside the shown matches does not make it (and focus) vanish.
  const [toggled, setToggled] = useState<ReadonlySet<string>>(() => new Set());
  // Discovery first, then anything chosen it did not return — the Linear
  // section's order, so the chips do not jump when one is toggled.
  const options = orderedUnion(repoLabels, labels);
  const full = labels.length >= GITHUB_LABELS_MAX;
  const searchable = repoLabels.length > MAX_LABEL_SUGGESTIONS;
  const needle = query.trim().toLocaleLowerCase();
  const matches = repoLabels.filter(
    (name) =>
      !labels.includes(name) && (needle === '' || name.toLocaleLowerCase().includes(needle)),
  );
  const shownMatches = new Set(matches.slice(0, MAX_LABEL_SUGGESTIONS));
  const hiddenCount = matches.length - shownMatches.size;
  const visible = options.filter(
    (name) => labels.includes(name) || shownMatches.has(name) || toggled.has(name),
  );

  const toggle = (name: string) => {
    setToggled((current) => new Set(current).add(name));
    const next = new Set(labels);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange(options.filter((option) => next.has(option)));
  };

  return (
    <fieldset className="chip-group autoscan-chip-group">
      <legend>Labels to pick up</legend>
      {status === 'loading' && (
        <p className="field-hint state-loading">Loading the repository’s labels…</p>
      )}
      {status === 'error' && (
        <div className="autoscan-inline-error">
          <p className="field-error">Couldn’t load the repository’s labels: {error}</p>
          <button type="button" className="button" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}
      {status === 'ready' && repoLabels.length === 0 && (
        <p className="field-hint">
          This repository has no labels yet. Create one on GitHub, then check again.
        </p>
      )}
      {searchable && (
        <TextField
          label="Find a label"
          placeholder="Type part of a name"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(value) => {
            setQuery(value);
            setToggled(new Set());
          }}
        />
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
      {searchable && needle !== '' && matches.length === 0 && (
        <p className="field-hint">No label matches “{query.trim()}”.</p>
      )}
      {hiddenCount > 0 && (
        <p className="field-hint">
          {hiddenCount} more {hiddenCount === 1 ? 'label' : 'labels'}: search to find them.
        </p>
      )}
      {full && (
        <p className="field-hint">You can pick up at most {GITHUB_LABELS_MAX} labels.</p>
      )}
    </fieldset>
  );
}

// GitHub's issue type colours (its colour names), as the swatch beside a type.
const ISSUE_TYPE_COLORS: Readonly<Record<string, string>> = {
  GRAY: '#8b949e',
  BLUE: '#4493f8',
  GREEN: '#3fb950',
  YELLOW: '#d29922',
  ORANGE: '#db6d28',
  RED: '#f85149',
  PINK: '#db61a2',
  PURPLE: '#ab7df8',
};

function sameType(a: string, b: string): boolean {
  return a.toLocaleLowerCase() === b.toLocaleLowerCase();
}

/**
 * The GitHub issue types to take, as chips of the repository's types. Names
 * match without regard to case, as on the agent. Chosen types the repository
 * does not list stay on as chips, so they can be unchecked.
 */
function GitHubIssueTypesField({
  chosen,
  repoTypes,
  status,
  onChange,
}: {
  chosen: readonly string[];
  repoTypes: readonly GitHubIssueType[];
  status: AutoScanSourcesView['githubLabels']['status'];
  onChange: (types: string[]) => void;
}) {
  const extra = chosen.filter((name) => !repoTypes.some((type) => sameType(type.name, name)));
  const options: { name: string; color: string; description: string }[] = [
    ...repoTypes.map((type) => ({
      name: type.name,
      color: type.color,
      description: type.description,
    })),
    ...extra.map((name) => ({ name, color: '', description: '' })),
  ];
  const full = chosen.length >= GITHUB_ISSUE_TYPES_MAX;

  const toggle = (name: string) => {
    if (chosen.some((type) => sameType(type, name))) {
      onChange(chosen.filter((type) => !sameType(type, name)));
    } else {
      onChange([...chosen, name]);
    }
  };

  return (
    <fieldset className="chip-group autoscan-chip-group">
      <legend>Issue types</legend>
      <p className="field-hint">Only issues of the chosen types are picked up. None chosen: any type.</p>
      {status === 'ready' && repoTypes.length === 0 && (
        <p className="field-hint">This repository has no issue types.</p>
      )}
      {options.length > 0 && (
        <div className="chip-row">
          {options.map((option) => {
            const checked = chosen.some((type) => sameType(type, option.name));
            const swatch = Object.hasOwn(ISSUE_TYPE_COLORS, option.color)
              ? ISSUE_TYPE_COLORS[option.color]
              : undefined;
            return (
              <label
                key={option.name}
                className="chip"
                title={option.description !== '' ? option.description : undefined}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!checked && full}
                  onChange={() => {
                    toggle(option.name);
                  }}
                />
                {swatch !== undefined && (
                  <span
                    className="autoscan-type-swatch"
                    style={{ background: swatch }}
                    aria-hidden="true"
                  />
                )}
                {option.name}
              </label>
            );
          })}
        </div>
      )}
    </fieldset>
  );
}
