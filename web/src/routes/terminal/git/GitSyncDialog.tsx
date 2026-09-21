import { useRef, useState } from 'react';
import type {
  SessionGitJob,
  SessionGitRepoInfo,
  SessionGitSyncRequest,
} from '../../../api/sessionGitRepository';
import { Modal } from '../../../components/Modal';
import type { GitCapabilities } from '../../../protocol/dto/agentVersion';
import {
  JOB_LOST_COPY,
  followUpLabel,
  followUpPrompt,
  followUpRequest,
  headLabel,
  jobDoneTitle,
  jobErrorCopy,
  jobFollowUps,
  jobTitle,
  showsJobOutput,
  syncRequestFor,
  syncRows,
  upstreamLabel,
  type SyncFollowUp,
} from './sessionGitDisplay';

interface GitSyncDialogProps {
  repo: SessionGitRepoInfo | null | undefined;
  capabilities: GitCapabilities;
  /** The session's current job, if the panel is tracking one. */
  job: SessionGitJob | undefined;
  /** The reconcile poll answered `job_not_found`. */
  jobLost: boolean;
  /** A start request is in flight — the only moment the sheet cannot be dismissed. */
  starting: boolean;
  startError: string | null;
  onStart: (request: SessionGitSyncRequest) => void;
  onCancelJob: (jobId: string) => void;
  onClose: () => void;
}

/**
 * The Fetch / Pull / Push sheet: the head and upstream line, one row per
 * action with its reason when it cannot run, then — once a job exists — its
 * progress with Cancel, or its outcome with the follow-ups a failure allows.
 * A modal `<dialog>` over the manual popover like the commit sheet; the
 * confirmation a follow-up needs is a second, nested modal whose initial
 * focus sits on Cancel.
 *
 * The sheet may be closed while a job runs — the panel's banner carries on —
 * so only the start request itself blocks dismissal.
 */
export function GitSyncDialog({
  repo,
  capabilities,
  job,
  jobLost,
  starting,
  startError,
  onStart,
  onCancelJob,
  onClose,
}: GitSyncDialogProps) {
  const [pendingFollowUp, setPendingFollowUp] = useState<SyncFollowUp | null>(null);
  const cancelFollowUpRef = useRef<HTMLButtonElement>(null);
  const rows = syncRows(repo, capabilities);
  const running = job !== undefined && job.status !== 'done';
  const locked = starting || running;
  const upstream = upstreamLabel(repo?.upstream);
  const prompt = pendingFollowUp === null ? null : followUpPrompt(pendingFollowUp, repo);

  return (
    <Modal labelledBy="git-sync-title" dismissible={!starting} onClose={onClose} className="git-sync">
      <h2 id="git-sync-title">Fetch / Pull / Push</h2>
      <p className="git-sync-summary">
        <span className="git-changes-branch">{repo === null || repo === undefined ? '—' : headLabel(repo)}</span>
        <span className="git-sync-upstream">{upstream ?? 'No upstream'}</span>
      </p>
      <div className="git-sync-rows">
        {rows.map((row) => (
          <button
            key={row.op}
            type="button"
            className="button git-sync-row"
            disabled={!row.enabled || locked}
            onClick={() => {
              onStart(syncRequestFor(row.op));
            }}
          >
            <span className="git-sync-row-label">{row.label}</span>
            <span className="git-sync-row-detail">{row.detail}</span>
          </button>
        ))}
      </div>
      {startError !== null && (
        <p className="field-error" role="alert">
          {startError}
        </p>
      )}
      {jobLost && (
        <p className="field-error" role="alert">
          {JOB_LOST_COPY}
        </p>
      )}
      {job !== undefined && running && (
        <div className="git-sync-progress" role="status" aria-live="polite">
          <div className="git-sync-progress-head">
            <strong>{jobTitle(job.op)}</strong>
            {job.phase !== null && job.phase !== undefined && (
              <span className="git-sync-phase">
                {job.phase}
                {job.percent !== null && job.percent !== undefined ? ` ${String(job.percent)}%` : ''}
              </span>
            )}
          </div>
          <SyncProgressBar percent={job.percent ?? null} label={job.phase ?? jobTitle(job.op)} />
          {job.line !== null && job.line !== undefined && job.line !== '' && (
            <code className="git-sync-line">{job.line}</code>
          )}
          <div className="button-row">
            <button
              type="button"
              className="button"
              onClick={() => {
                onCancelJob(job.job_id);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {job !== undefined && !running && (
        <div
          className="git-sync-result"
          data-tone={job.ok === true ? 'ok' : 'danger'}
          role={job.ok === true ? 'status' : 'alert'}
        >
          <strong>{jobDoneTitle(job.op, job.ok === true)}</strong>
          {job.ok === true ? (
            job.message !== null && job.message !== undefined && job.message !== '' && (
              <code className="git-sync-line">{job.message}</code>
            )
          ) : (
            <>
              <span>{jobErrorCopy(job.error_code, job.message, capabilities.conflicts)}</span>
              {showsJobOutput(job) && <pre className="git-commit-output">{job.message}</pre>}
            </>
          )}
          {jobFollowUps(job, repo).length > 0 && (
            <div className="button-row">
              {jobFollowUps(job, repo).map((followUp) => (
                <button
                  key={followUp}
                  type="button"
                  className={followUp === 'force_push' ? 'button button-danger' : 'button'}
                  disabled={locked}
                  onClick={() => {
                    setPendingFollowUp(followUp);
                  }}
                >
                  {followUpLabel(followUp)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="button-row">
        <button type="button" className="button" disabled={starting} onClick={onClose}>
          Close
        </button>
      </div>
      {pendingFollowUp !== null && prompt !== null && (
        <Modal
          role="alertdialog"
          labelledBy="git-sync-confirm-title"
          initialFocus={cancelFollowUpRef}
          onClose={() => {
            setPendingFollowUp(null);
          }}
        >
          <h2 id="git-sync-confirm-title">{prompt.title}</h2>
          <p>{prompt.body}</p>
          <div className="button-row">
            <button
              type="button"
              className={pendingFollowUp === 'force_push' ? 'button button-danger' : 'button button-primary'}
              onClick={() => {
                const request = followUpRequest(pendingFollowUp);
                setPendingFollowUp(null);
                onStart(request);
              }}
            >
              {prompt.confirm}
            </button>
            <button
              type="button"
              ref={cancelFollowUpRef}
              className="button"
              onClick={() => {
                setPendingFollowUp(null);
              }}
            >
              Cancel
            </button>
          </div>
        </Modal>
      )}
    </Modal>
  );
}

/**
 * A two-rectangle pixel bar. No width transition: git's percentage restarts
 * at every phase (Counting → Compressing → Writing), and an animated fill
 * would run backwards each time. `null` is the indeterminate state, drawn as
 * the theme's blinking block.
 */
export function SyncProgressBar({ percent, label }: { percent: number | null; label: string }) {
  if (percent === null) {
    return <span className="git-sync-bar git-sync-bar-indeterminate state-loading" aria-label={label} />;
  }
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <span
      className="git-sync-bar"
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
    >
      <span className="git-sync-bar-fill" style={{ width: `${String(clamped)}%` }} />
    </span>
  );
}
