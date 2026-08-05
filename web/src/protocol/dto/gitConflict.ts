/**
 * The git-delete busy conflict, pinned by
 * `tests/protocol_fixtures/dto/agent/{BusyHolder,BusyByResponse}.json`.
 *
 * Hand-written because this shape appears in **neither** OpenAPI schema: it is
 * a 409 body, and FastAPI documents only the success responses. The agent packs
 * it into `detail` (`agent/git/router.py`), and the relay re-wraps it there too,
 * so the wire body is `{"detail": {"busy_by": [...]}}` on both paths.
 */
import { z } from 'zod';
import { optionalWireString } from '../wireSchema';

/**
 * One reason a delete was refused. `kind` stays an open string rather than a
 * `session | auto_scan` union: the wire policy is that kind fields are never
 * narrowed, and the golden's `minimal` case carries `autoscan` — a spelling the
 * server does not emit but a decoder must survive.
 */
export const busyHolderSchema = z.looseObject({
  kind: z.string(),
  session_id: optionalWireString(),
  project_name: optionalWireString(),
  cwd: optionalWireString(),
  worktree_path: optionalWireString(),
});

export const busyByResponseSchema = z.looseObject({
  busy_by: z.array(busyHolderSchema),
});

export type BusyHolder = z.infer<typeof busyHolderSchema>;
