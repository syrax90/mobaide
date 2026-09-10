import { Fragment, type CSSProperties } from 'react';
import {
  isSessionGitError,
  isTerminalSessionGitError,
  type SessionGitDiff,
  type SessionGitDiffRequest,
} from '../../../api/sessionGitRepository';
import { SessionGitState } from './SessionGitState';
import {
  DIFF_CONTEXT_CHOICES,
  changeLabel,
  diffScopeFor,
  diffView,
  fileName,
  sessionGitErrorCopy,
  type ChangesRow,
  type DiffHunkView,
} from './sessionGitDisplay';
import { useSessionGitDiff } from './useSessionGit';

interface GitDiffPaneProps {
  agentId: string;
  sessionId: string;
  selection: ChangesRow | null;
  context: number;
  onContextChange: (context: number) => void;
  /** Stacked layout only: back to the file list. */
  onBack: () => void;
}

export function GitDiffPane({
  agentId,
  sessionId,
  selection,
  context,
  onContextChange,
  onBack,
}: GitDiffPaneProps) {
  if (selection === null) {
    return <p className="git-diff-placeholder empty-state">Select a file to see its changes</p>;
  }
  return (
    <GitDiffView
      // A fresh mount per file, so nothing of the previous diff lingers.
      key={selection.key}
      agentId={agentId}
      sessionId={sessionId}
      selection={selection}
      context={context}
      onContextChange={onContextChange}
      onBack={onBack}
    />
  );
}

interface GitDiffViewProps extends Omit<GitDiffPaneProps, 'selection'> {
  selection: ChangesRow;
}

function GitDiffView({
  agentId,
  sessionId,
  selection,
  context,
  onContextChange,
  onBack,
}: GitDiffViewProps) {
  const entry = selection.entry;
  const request: SessionGitDiffRequest = {
    path: entry.path,
    scope: diffScopeFor(entry),
    context,
    ...(entry.orig_path === undefined || entry.orig_path === null
      ? {}
      : { origPath: entry.orig_path }),
  };
  const diff = useSessionGitDiff(agentId, sessionId, request);
  const origPath = entry.orig_path ?? null;

  return (
    <>
      <div className="git-diff-head">
        <div className="git-diff-title">
          <button type="button" className="button git-panel-back" onClick={onBack}>
            ← Changes
          </button>
          <h3>{fileName(entry.path)}</h3>
        </div>
        <span className="git-diff-path">{entry.path}</span>
        {origPath !== null && <span className="git-diff-path">from {origPath}</span>}
        <div className="git-diff-meta">
          {diff.data !== undefined && <DiffMeta diff={diff.data} />}
          <div className="git-diff-context" role="group" aria-label="Context lines">
            <span className="git-diff-context-label" aria-hidden="true">
              Context
            </span>
            {DIFF_CONTEXT_CHOICES.map((choice) => (
              <button
                key={choice}
                type="button"
                className="button"
                aria-pressed={choice === context}
                onClick={() => {
                  onContextChange(choice);
                }}
              >
                {choice}
              </button>
            ))}
          </div>
        </div>
      </div>
      <DiffBody
        path={entry.path}
        diff={diff.data}
        error={diff.error}
        onRetry={() => {
          void diff.refetch();
        }}
      />
    </>
  );
}

function DiffMeta({ diff }: { diff: SessionGitDiff }) {
  const change = diff.change ?? null;
  const oldMode = diff.old_mode ?? null;
  const newMode = diff.new_mode ?? null;
  const counted = diff.kind === 'text' || diff.kind === 'submodule';
  return (
    <>
      {change !== null && <span className="git-chip git-chip-neutral">{changeLabel(change)}</span>}
      {counted && (
        <span className="git-diff-stats">
          <span className="git-diff-stat-add">+{diff.additions}</span>{' '}
          <span className="git-diff-stat-del">−{diff.deletions}</span>
        </span>
      )}
      {oldMode !== null && newMode !== null && oldMode !== newMode && (
        <span className="git-diff-mode">
          {oldMode} → {newMode}
        </span>
      )}
    </>
  );
}

interface DiffBodyProps {
  path: string;
  diff: SessionGitDiff | undefined;
  error: unknown;
  onRetry: () => void;
}

/** Placeholder height per row while a hunk is off screen; only a scrollbar estimate. */
const ROW_PX = 19;
const HUNK_HEAD_PX = 28;

function DiffBody({ path, diff, error, onRetry }: DiffBodyProps) {
  if (isTerminalSessionGitError(error) && isSessionGitError(error)) {
    return error.kind === 'agentOutdated' ? (
      <SessionGitState
        title="Update your agent"
        body="This agent does not serve the git Changes API yet. Update it to view diffs here."
      />
    ) : (
      <SessionGitState title="Session ended" body="This session is no longer running on the agent." />
    );
  }
  if (diff === undefined) {
    if (error !== undefined && error !== null) {
      return (
        <SessionGitState
          title="Couldn't load diff"
          body={sessionGitErrorCopy(error)}
          action={{ label: 'Retry', onClick: onRetry }}
        />
      );
    }
    return <p className="page-note state-loading">Loading diff…</p>;
  }
  const view = diffView(diff);
  const style = { '--git-gutter': `${String(view.gutterDigits + 1)}ch` } as CSSProperties;
  return (
    <div className="git-diff-body" role="region" aria-label={`Changes to ${path}`} style={style}>
      {error !== undefined && error !== null && (
        <div className="git-changes-stale" role="alert">
          <span>{sessionGitErrorCopy(error)}</span>
          <button type="button" className="button" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}
      {view.notice !== null && <p className="git-diff-notice">{view.notice}</p>}
      {view.hunks.map((hunk) => (
        <Hunk key={hunk.key} hunk={hunk} />
      ))}
      {view.truncated !== null && <p className="git-diff-truncated">{view.truncated}</p>}
    </div>
  );
}

function Hunk({ hunk }: { hunk: DiffHunkView }) {
  // The height longhand only: the shorthand would also pin an intrinsic
  // *width* on a skipped hunk, inflating the horizontal scroll extent.
  const style: CSSProperties = {
    containIntrinsicHeight: `auto ${String(HUNK_HEAD_PX + hunk.lines.length * ROW_PX)}px`,
  };
  return (
    <div className="git-hunk" style={style}>
      <div className="git-hunk-head">{hunk.header}</div>
      {hunk.lines.map((line) => (
        <Fragment key={line.key}>
          <div className={`git-line git-line-${line.t}`}>
            {/* Gutters and marker are generated content: never inside a
                selection range, so a copy takes the code and nothing else. */}
            <span
              className="git-gutter"
              aria-hidden="true"
              data-ln={line.old === null ? '' : String(line.old)}
            />
            <span
              className="git-gutter"
              aria-hidden="true"
              data-ln={line.new === null ? '' : String(line.new)}
            />
            <span
              className="git-marker"
              aria-hidden="true"
              data-mark={line.t === 'add' ? '+' : line.t === 'del' ? '-' : ' '}
            />
            <span className="git-text">{line.text}</span>
          </div>
          {line.noNewline && (
            <div className="git-line git-line-nonl">\ No newline at end of file</div>
          )}
        </Fragment>
      ))}
    </div>
  );
}
