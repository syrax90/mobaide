import {
  isSessionGitError,
  isTerminalSessionGitError,
  type SessionGitEntry,
  type SessionGitStatus,
} from '../../../api/sessionGitRepository';
import type { GitCapabilities } from '../../../protocol/dto/agentVersion';
import { SessionGitState } from './SessionGitState';
import {
  badgeFor,
  directory,
  fileName,
  headLabel,
  repoStateLabel,
  rowActions,
  sectionAction,
  sectionsFor,
  selectionSubsets,
  sessionGitErrorCopy,
  truncatedEntriesNotice,
  unavailableCopy,
  upstreamLabel,
  type ChangesRow,
  type ChangesSection,
  type RowAction,
  type RowActions,
} from './sessionGitDisplay';

interface GitChangesListProps {
  status: SessionGitStatus | undefined;
  error: unknown;
  selectedKey: string | null;
  onSelect: (row: ChangesRow) => void;
  onRetry: () => void;
  capabilities: GitCapabilities;
  /** A mutation is in flight: every control that would start another waits. */
  busy: boolean;
  /** Keys of the rows ticked for the selection toolbar. */
  selectedKeys: ReadonlySet<string>;
  onToggleRow: (row: ChangesRow, selected: boolean) => void;
  onClearSelection: () => void;
  onAction: (action: RowAction, rows: readonly ChangesRow[]) => void;
  /** Opens the Fetch / Pull / Push sheet; absent on an agent without the jobs. */
  onSync?: (() => void) | undefined;
}

/**
 * The repo header and the grouped file list, with every state the #669
 * contract names. A failed poll over a cached body keeps the body on screen
 * and reports the failure above it, as both mobile clients do.
 *
 * Every mutating control — the per-row buttons, the section headers'
 * Stage all / Unstage all, the checkboxes and the selection toolbar — exists
 * only when the agent advertises the matching `capabilities.git` key; an
 * older agent gets the read-only list of #669.
 */
export function GitChangesList({
  status,
  error,
  selectedKey,
  onSelect,
  onRetry,
  capabilities,
  busy,
  selectedKeys,
  onToggleRow,
  onClearSelection,
  onAction,
  onSync,
}: GitChangesListProps) {
  if (isTerminalSessionGitError(error) && isSessionGitError(error)) {
    return error.kind === 'agentOutdated' ? (
      <SessionGitState
        title="Update your agent"
        body="This agent does not serve the git Changes API yet. Update it to view changes here."
      />
    ) : (
      <SessionGitState title="Session ended" body="This session is no longer running on the agent." />
    );
  }
  if (status === undefined) {
    if (error !== undefined && error !== null) {
      return (
        <SessionGitState
          title="Couldn't load changes"
          body={sessionGitErrorCopy(error)}
          action={{ label: 'Retry', onClick: onRetry }}
        />
      );
    }
    return <p className="page-note state-loading">Loading changes…</p>;
  }
  const reason = status.unavailable_reason;
  if (reason !== undefined && reason !== null) {
    const copy = unavailableCopy(reason);
    return (
      <SessionGitState
        title={copy.title}
        body={copy.body}
        detail={status.cwd ?? undefined}
        action={{ label: 'Retry', onClick: onRetry }}
      />
    );
  }

  const sections = sectionsFor(status);
  const truncated = truncatedEntriesNotice(status);
  const selectable = capabilities.stage || capabilities.discard;
  return (
    <>
      {status.repo !== undefined && status.repo !== null && (
        <RepoHeader status={status} onSync={onSync} />
      )}
      {error !== undefined && error !== null && (
        <div className="git-changes-stale" role="alert">
          <span>{sessionGitErrorCopy(error)}</span>
          <button type="button" className="button" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}
      {truncated !== null && <p className="git-changes-notice">{truncated}</p>}
      {selectable && selectedKeys.size > 0 && (
        <SelectionBar
          sections={sections}
          selectedKeys={selectedKeys}
          capabilities={capabilities}
          busy={busy}
          onAction={onAction}
          onClear={onClearSelection}
        />
      )}
      {sections.length === 0 ? (
        <p className="empty-state">Working tree clean</p>
      ) : (
        sections.map((section) => {
          const bulk = sectionAction(section, capabilities);
          return (
            <section key={section.group} className="git-changes-section">
              <h3>
                <span>{section.title}</span>
                <span className="git-changes-count">{section.rows.length}</span>
                {bulk !== null && (
                  <button
                    type="button"
                    className="button"
                    disabled={busy}
                    onClick={() => {
                      onAction(bulk.action, section.rows);
                    }}
                  >
                    {bulk.label}
                  </button>
                )}
              </h3>
              <ul className="git-changes-rows">
                {section.rows.map((row) => {
                  const actions = rowActions(row.entry, capabilities);
                  const hasBulkActions = actions.stage || actions.unstage || actions.discard;
                  const hasActions = hasBulkActions || actions.resolve;
                  return (
                    <li key={row.key} className="git-changes-item">
                      {/* A conflict has no checkbox rather than a disabled one: no
                          toolbar action applies to it — Mark resolved is one file
                          at a time — so a control would be noise. */}
                      {selectable && hasBulkActions && (
                        <input
                          type="checkbox"
                          className="git-changes-select"
                          checked={selectedKeys.has(row.key)}
                          disabled={busy}
                          aria-label={`Select ${row.entry.path}`}
                          onChange={(event) => {
                            onToggleRow(row, event.target.checked);
                          }}
                        />
                      )}
                      <EntryRow
                        entry={row.entry}
                        selected={row.key === selectedKey}
                        onClick={() => {
                          onSelect(row);
                        }}
                      />
                      {hasActions && (
                        <RowActionButtons
                          entry={row.entry}
                          actions={actions}
                          busy={busy}
                          onAction={(action) => {
                            onAction(action, [row]);
                          }}
                        />
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })
      )}
    </>
  );
}

interface SelectionBarProps {
  sections: readonly ChangesSection[];
  selectedKeys: ReadonlySet<string>;
  capabilities: GitCapabilities;
  busy: boolean;
  onAction: (action: RowAction, rows: readonly ChangesRow[]) => void;
  onClear: () => void;
}

/**
 * The multi-select toolbar: each action acts on the ticked rows it applies
 * to — Stage on modified and untracked ones, Unstage on staged ones, Discard on
 * the first two — and is enabled only while that subset is non-empty.
 */
function SelectionBar({
  sections,
  selectedKeys,
  capabilities,
  busy,
  onAction,
  onClear,
}: SelectionBarProps) {
  const subsets = selectionSubsets(sections, selectedKeys, capabilities);
  return (
    <div className="git-selection-bar" role="toolbar" aria-label="Selected files">
      <span className="git-selection-count">{selectedKeys.size} selected</span>
      {capabilities.stage && (
        <button
          type="button"
          className="button"
          disabled={busy || subsets.stage.length === 0}
          onClick={() => {
            onAction('stage', subsets.stage);
          }}
        >
          Stage
        </button>
      )}
      {capabilities.stage && (
        <button
          type="button"
          className="button"
          disabled={busy || subsets.unstage.length === 0}
          onClick={() => {
            onAction('unstage', subsets.unstage);
          }}
        >
          Unstage
        </button>
      )}
      {capabilities.discard && (
        <button
          type="button"
          className="button button-danger"
          disabled={busy || subsets.discard.length === 0}
          onClick={() => {
            onAction('discard', subsets.discard);
          }}
        >
          Discard…
        </button>
      )}
      <button
        type="button"
        className="button button-icon"
        aria-label="Clear selection"
        title="Clear selection"
        onClick={onClear}
      >
        ✕
      </button>
    </div>
  );
}

function RepoHeader({
  status,
  onSync,
}: {
  status: SessionGitStatus;
  onSync?: (() => void) | undefined;
}) {
  const repo = status.repo;
  if (repo === undefined || repo === null) return null;
  const state = repoStateLabel(repo.state);
  const upstream = upstreamLabel(repo.upstream);
  return (
    <div className="git-changes-repo">
      <div className="git-changes-head">
        <span className="git-changes-branch">{headLabel(repo)}</span>
        {state !== null && <span className="git-chip git-chip-danger">{state}</span>}
        {repo.is_linked_worktree && <span className="git-chip git-chip-muted">worktree</span>}
      </div>
      {/* The ahead/behind counts are what a fetch, pull or push acts on, so
          the line itself opens the sheet once the agent can run them. */}
      {upstream !== null &&
        (onSync === undefined ? (
          <span className="git-changes-upstream">{upstream}</span>
        ) : (
          <button
            type="button"
            className="git-changes-upstream git-changes-upstream-button"
            title="Fetch, pull or push"
            onClick={onSync}
          >
            {upstream}
          </button>
        ))}
      {status.cwd !== undefined && status.cwd !== null && (
        <span className="git-changes-cwd">{status.cwd}</span>
      )}
    </div>
  );
}

interface EntryRowProps {
  entry: SessionGitEntry;
  selected: boolean;
  onClick: () => void;
}

function EntryRow({ entry, selected, onClick }: EntryRowProps) {
  const badge = badgeFor(entry);
  const origPath = entry.orig_path ?? null;
  const secondary = origPath !== null ? `${origPath} → ${entry.path}` : directory(entry.path);
  return (
    <button type="button" className="git-changes-row" aria-pressed={selected} onClick={onClick}>
      <span className={`git-badge git-badge-${badge.tone}`} aria-label={badge.tone}>
        {badge.label}
      </span>
      <span className="git-changes-text">
        <span className="git-changes-name">{fileName(entry.path)}</span>
        {secondary !== null && <span className="git-changes-dir">{secondary}</span>}
      </span>
      {entry.submodule && <span className="git-chip git-chip-muted">submodule</span>}
    </button>
  );
}

interface RowActionButtonsProps {
  entry: SessionGitEntry;
  actions: RowActions;
  busy: boolean;
  onAction: (action: RowAction) => void;
}

/**
 * The quick actions beside a row: shown on hover or keyboard focus with a
 * pointer, always on a touch screen (`global.css`). Discard is two steps — the
 * button only opens the confirmation.
 */
function RowActionButtons({ entry, actions, busy, onAction }: RowActionButtonsProps) {
  return (
    <span className="git-changes-row-actions">
      {actions.resolve && (
        <button
          type="button"
          className="button button-icon"
          disabled={busy}
          aria-label={`Mark ${entry.path} resolved`}
          title="Mark resolved"
          onClick={() => {
            onAction('resolve');
          }}
        >
          ✓
        </button>
      )}
      {actions.stage && (
        <button
          type="button"
          className="button button-icon"
          disabled={busy}
          aria-label={`Stage ${entry.path}`}
          title="Stage"
          onClick={() => {
            onAction('stage');
          }}
        >
          +
        </button>
      )}
      {actions.unstage && (
        <button
          type="button"
          className="button button-icon"
          disabled={busy}
          aria-label={`Unstage ${entry.path}`}
          title="Unstage"
          onClick={() => {
            onAction('unstage');
          }}
        >
          −
        </button>
      )}
      {actions.discard && (
        <button
          type="button"
          className="button button-icon button-danger"
          disabled={busy}
          aria-label={`Discard ${entry.path}`}
          title="Discard…"
          onClick={() => {
            onAction('discard');
          }}
        >
          ✕
        </button>
      )}
    </span>
  );
}
