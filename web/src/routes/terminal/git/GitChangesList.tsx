import {
  isSessionGitError,
  isTerminalSessionGitError,
  type SessionGitEntry,
  type SessionGitStatus,
} from '../../../api/sessionGitRepository';
import { SessionGitState } from './SessionGitState';
import {
  badgeFor,
  directory,
  fileName,
  headLabel,
  repoStateLabel,
  sectionsFor,
  sessionGitErrorCopy,
  truncatedEntriesNotice,
  unavailableCopy,
  upstreamLabel,
  type ChangesRow,
} from './sessionGitDisplay';

interface GitChangesListProps {
  status: SessionGitStatus | undefined;
  error: unknown;
  selectedKey: string | null;
  onSelect: (row: ChangesRow) => void;
  onRetry: () => void;
}

/**
 * The repo header and the grouped file list, with every state the #669
 * contract names. A failed poll over a cached body keeps the body on screen
 * and reports the failure above it, as both mobile clients do.
 */
export function GitChangesList({ status, error, selectedKey, onSelect, onRetry }: GitChangesListProps) {
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
  return (
    <>
      {status.repo !== undefined && status.repo !== null && (
        <RepoHeader status={status} />
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
      {sections.length === 0 ? (
        <p className="empty-state">Working tree clean</p>
      ) : (
        sections.map((section) => (
          <section key={section.group} className="git-changes-section">
            <h3>
              <span>{section.title}</span>
              <span className="git-changes-count">{section.rows.length}</span>
            </h3>
            <ul className="git-changes-rows">
              {section.rows.map((row) => (
                <li key={row.key}>
                  <EntryRow
                    entry={row.entry}
                    selected={row.key === selectedKey}
                    onClick={() => {
                      onSelect(row);
                    }}
                  />
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </>
  );
}

function RepoHeader({ status }: { status: SessionGitStatus }) {
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
      {upstream !== null && <span className="git-changes-upstream">{upstream}</span>}
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
