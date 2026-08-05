/**
 * Auto Scan wire shapes, pinned by `tests/protocol_fixtures/dto/agent/AutoScanInstanceDto.json`
 * and `AutoScanListResponse.json`.
 *
 * Hand-written rather than taken from the generated agent schema for the reason
 * `session.ts` and `projects.ts` give: these shapes arrive over a socket
 * (`autoscan.status`, `autoscan.list.changed`, and the `ws/autoscan` snapshot)
 * and are written straight into the query cache. The REST list decodes through
 * the same schemas so both writers agree byte-for-byte.
 *
 * `parseScanPayload` is the TypeScript arm of `ScanPayloadParser` on both mobile
 * clients: it turns one `(type, payload)` pair into a discriminated union and
 * degrades anything it cannot model to `unknown` rather than throwing, because
 * it runs inside the socket's message loop.
 */
import { z } from 'zod';
import { optionalWireNumber, optionalWireString } from '../wireSchema';

/**
 * One running (or recently stopped) scan — the agent's `AutoScanInstance.to_dto`.
 *
 * `current_seq` accepts a `bigint` because the same object arrives inside the
 * `ws/autoscan` snapshot, which is decoded by `parseWireJson` and therefore
 * promotes an out-of-range integer literal. It narrows to `number` on the way
 * out: this field is display-only, and the cursor that must not lose precision
 * is the per-event `seq` in `autoscanFrames.ts`, which stays `bigint` end to end.
 *
 * `processed_count` counts this project's active work items, not issues seen in
 * a cycle — the agent keeps the name for DTO compatibility.
 */
export const autoScanInstanceSchema = z.looseObject({
  project_name: z.string(),
  /** Open string: `idle` | `scanning` | `stopped` | `error`, or anything newer. */
  status: z.string(),
  started_at: z.number(),
  last_activity_at: z.number(),
  processed_count: z.number().default(0),
  last_scan_time: optionalWireNumber(),
  scan_interval_seconds: z.number().default(300),
  next_scan_at: optionalWireString(),
  cycle_number: z.number().default(0),
  current_seq: z
    .union([z.number(), z.bigint()])
    .nullish()
    .transform((value) => (value === null || value === undefined ? 0 : Number(value))),
});

/**
 * `GET /api/autoscan/list`, and the payload of the `autoscan.list.changed`
 * event.
 *
 * Every field defaults, because the relay answers a request made while the
 * agent has no Auto Scan manager with a partial body (`relay/client.py`), and
 * `auto_scan_prompt` is absent from that shape entirely.
 *
 * `capacity` is the number of scans *running*, not the headroom left — the
 * remaining slots are `max_scans - capacity`.
 */
export const autoScanListResponseSchema = z.looseObject({
  scans: z.array(autoScanInstanceSchema).default([]),
  capacity: z.number().default(0),
  max_scans: z.number().default(5),
  available_projects: z.array(z.string()).default([]),
  auto_scan_prompt: optionalWireString(),
});

const scanPhasePayloadSchema = z.looseObject({
  /** `scan_started` | `scan_completed` | `scan_stopped`. */
  phase: z.string(),
  cycle_number: z.number().default(0),
  next_scan_at: optionalWireString(),
  issues_processed: z.number().default(0),
});

const scanIssuePayloadSchema = z.looseObject({
  identifier: z.string(),
  title: z.string().default(''),
  /** `state_update` | `label_removal` | `worktree_create` | `session_spawn`. */
  step: z.string(),
  /** `started` | `ok` | `failed`. */
  status: z.string(),
  /**
   * Present alongside `ok` as well as `failed`: a `label_removal` for a label
   * the issue never carried succeeds *and* explains itself.
   */
  error: optionalWireString(),
});

const scanWorktreePayloadSchema = z.looseObject({
  identifier: z.string(),
  path: z.string(),
});

const scanSessionPayloadSchema = z.looseObject({
  identifier: z.string(),
  session_id: z.string(),
  pid: optionalWireNumber(),
});

const scanLogPayloadSchema = z.looseObject({
  line: z.string(),
  /** `info` | `warn` | `error`. */
  level: z.string().default('info'),
  ansi: z.boolean().default(false),
});

export type AutoScanInstance = z.infer<typeof autoScanInstanceSchema>;
export type AutoScanListResponse = z.infer<typeof autoScanListResponseSchema>;

export type ScanPayload =
  | ({ type: 'phase' } & z.infer<typeof scanPhasePayloadSchema>)
  | ({ type: 'issue' } & z.infer<typeof scanIssuePayloadSchema>)
  | ({ type: 'worktree' } & z.infer<typeof scanWorktreePayloadSchema>)
  | ({ type: 'session' } & z.infer<typeof scanSessionPayloadSchema>)
  | ({ type: 'log' } & z.infer<typeof scanLogPayloadSchema>)
  /** An event type this client does not model, or a payload that failed its schema. */
  | { type: 'unknown' };

const PAYLOAD_SCHEMAS = {
  phase: scanPhasePayloadSchema,
  issue: scanIssuePayloadSchema,
  worktree: scanWorktreePayloadSchema,
  session: scanSessionPayloadSchema,
  log: scanLogPayloadSchema,
} as const;

const UNKNOWN_PAYLOAD: ScanPayload = { type: 'unknown' };

/**
 * Decode one scan event's payload by its `type`.
 *
 * Never throws: an unmodelled type and a malformed payload both become
 * `unknown`, which the reducer skips. `Object.hasOwn`, not a bare index —
 * `type` is wire data, and a frame naming an `Object.prototype` key would
 * otherwise resolve to an inherited function.
 */
export function parseScanPayload(type: string, payload: unknown): ScanPayload {
  if (!Object.hasOwn(PAYLOAD_SCHEMAS, type)) return UNKNOWN_PAYLOAD;
  const schema = PAYLOAD_SCHEMAS[type as keyof typeof PAYLOAD_SCHEMAS];
  const decoded = schema.safeParse(payload);
  if (!decoded.success) return UNKNOWN_PAYLOAD;
  // `type` **after** the spread, not before. These are `looseObject` schemas, so
  // a `type` key inside the payload survives parsing — and spread-last would let
  // the wire pick which branch of the reducer runs, handing an `issue` payload
  // to the `log` arm and putting `undefined` where a line of text belongs.
  return { ...decoded.data, type } as ScanPayload;
}
