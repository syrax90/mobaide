/**
 * `git.job.progress` and `git.job.done`, applied straight to the query cache.
 *
 * Both carry the whole `GitJobResponse`, so they qualify for the
 * write-rather-than-invalidate exception `queryBridge.ts` states — and
 * invalidating would be the per-log-line REST storm the agent's 4/s throttle
 * exists to avoid. Two entries are written: the job itself, under its own id,
 * and a per-session pointer to the newest job, which is how a panel adopts a
 * job another client (or another tab) started.
 *
 * Monotonic: a `running` frame never overwrites a `done` one. The reconcile
 * poll and the events race — a `GET` issued just before `done` landed can
 * answer after it — and the terminal state must win that race.
 */
import type { QueryClient } from '@tanstack/react-query';
import type { SessionGitJob } from '../api/sessionGitRepository';
import { queryKeys } from '../lib/queryKeys';
import { gitJobSchema } from '../protocol/dto/gitJob';

export interface SessionGitLatestJob {
  jobId: string;
}

export function applySessionGitJob(client: QueryClient, agentId: string, job: SessionGitJob): void {
  client.setQueryData<SessionGitJob>(
    queryKeys.sessionGitJob(agentId, job.session_id, job.job_id),
    (previous) => (previous?.status === 'done' ? previous : job),
  );
  client.setQueryData<SessionGitLatestJob>(
    queryKeys.sessionGitLatestJob(agentId, job.session_id),
    (previous) => (previous?.jobId === job.job_id ? previous : { jobId: job.job_id }),
  );
}

export function applySessionGitJobEvent(
  client: QueryClient,
  agentId: string,
  payload: unknown,
): 'applied' | 'dropped' {
  const decoded = gitJobSchema.safeParse(payload);
  if (!decoded.success) return 'dropped';
  applySessionGitJob(client, agentId, decoded.data);
  return 'applied';
}
