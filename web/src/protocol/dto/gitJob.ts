/**
 * `git.job.progress` / `git.job.done` — the payload of both is one whole
 * `GitJobResponse`, the same body `POST …/git/fetch|pull|push` and
 * `GET …/git/jobs/{id}` answer, so a frame is written straight into the job's
 * cache entry (a snapshot, never a delta). The REST side reads the generated
 * type; this mirror is for the socket, where nothing is typed until parsed.
 *
 * `op` and `status` are closed on purpose: a job the client cannot name has no
 * row to render, and dropping such a frame leaves the 3 s reconcile poll —
 * the source of truth — to show whatever the agent answers.
 */
import { z } from 'zod';
import { optionalWireBoolean, optionalWireNumber, optionalWireString } from '../wireSchema';

export const gitJobSchema = z.looseObject({
  job_id: z.string(),
  session_id: z.string(),
  op: z.enum(['fetch', 'pull', 'push']),
  status: z.enum(['running', 'done']),
  phase: optionalWireString(),
  percent: optionalWireNumber(),
  line: optionalWireString(),
  ok: optionalWireBoolean(),
  error_code: optionalWireString(),
  message: optionalWireString(),
});

export type GitJobPayload = z.infer<typeof gitJobSchema>;
