/**
 * Reduction of the `ws/autoscan` frame stream into detail-screen state.
 *
 * Port of iOS `AutoScanDetailModel` (`ios/Mobaide/UI/AutoScan/AutoScanDetailModel.swift`).
 * Framework-free and pure, like `src/terminal/`'s reducers — React only owns the
 * `useReducer` call in `useAutoScanDetail.ts`.
 *
 * **Idempotency invariant.** `lastSeq` is the *reduction* cursor, the second of
 * the two layers this feature needs: the wire cursor in `protocol/autoscanFrames.ts`
 * drops replays inside one channel, and this one makes reducing the same stream
 * twice — after a full remount, or a snapshot overlapping live events — produce
 * identical state. Android has neither, which is why its log duplicates lines
 * (CLA-119); iOS has both and this mirrors iOS.
 *
 * It is `bigint` for the same reason the wire cursor is: `seq` is an unbounded
 * per-instance counter, and a `number` comparison silently stops discriminating
 * past 2^53.
 */
import type { AutoScanEvent, AutoScanFrame } from '../protocol/autoscanFrames';
import {
  autoScanInstanceSchema,
  parseScanPayload,
  type AutoScanInstance,
  type ScanPayload,
} from '../protocol/dto/autoscan';

/** The timeline, in the order it renders. Never sorted from the wire. */
export const PHASE_ORDER = ['scan', 'state', 'label', 'worktree', 'spawn'] as const;

export type PhaseKey = (typeof PHASE_ORDER)[number];
export type PhaseStatus = 'pending' | 'active' | 'ok' | 'failed' | 'skipped';

const PHASE_KEYS = new Set<string>(PHASE_ORDER);

/** How many log lines stay resident. Matches both mobile clients. */
export const LOG_RING_MAX = 2000;
const RECENT_ISSUES_MAX = 50;

export interface IssueRow {
  identifier: string;
  title: string;
  step: string;
  status: string;
  error?: string;
}

/**
 * One rendered log line.
 *
 * `id` is a monotonic counter that is **never recycled** and is **not** the event
 * `seq`: one event can append two lines (an idle `scan_completed` says so twice),
 * so `seq` would collide. It is the React key.
 *
 * `text` is stored raw and parsed at render, so the reducer stays synchronous and
 * a line that scrolls out of view costs nothing. iOS parses eagerly instead
 * because it has to hop off the main actor to do it.
 */
export interface LogLine {
  id: number;
  text: string;
  ansi: boolean;
}

export type DetailConnection = 'connecting' | 'replaying' | 'connected' | 'disconnected';

/**
 * Socket state is not on the wire — the agent sends only `snapshot`,
 * `caught_up` and `event` — so the channel's open/close is folded in here.
 */
export type DetailFrame = { kind: 'connected' } | { kind: 'disconnected' } | AutoScanFrame;

export interface AutoScanDetailState {
  readonly projectName: string;
  readonly instance?: AutoScanInstance;
  readonly nextScanAt?: string;
  readonly cycleNumber: number;
  readonly activePhase?: string;
  readonly phases: Readonly<Record<PhaseKey, PhaseStatus>>;
  readonly recentIssues: readonly IssueRow[];
  readonly logLines: readonly LogLine[];
  readonly connection: DetailConnection;
  /** Reduction cursor; also what a reconnect resumes from. */
  readonly lastSeq: bigint;
  readonly nextLogId: number;
}

function freshPhases(activeScan: boolean): Record<PhaseKey, PhaseStatus> {
  return {
    scan: activeScan ? 'active' : 'pending',
    state: 'pending',
    label: 'pending',
    worktree: 'pending',
    spawn: 'pending',
  };
}

export function initialAutoScanDetailState(projectName: string): AutoScanDetailState {
  return {
    projectName,
    cycleNumber: 0,
    phases: freshPhases(false),
    recentIssues: [],
    logLines: [],
    connection: 'connecting',
    lastSeq: 0n,
    nextLogId: 0,
  };
}

/** The agent's issue `step` names, mapped onto timeline keys. */
function stepToPhase(step: string): string {
  switch (step) {
    case 'state_update':
      return 'state';
    case 'label_removal':
      return 'label';
    case 'worktree_create':
      return 'worktree';
    case 'session_spawn':
      return 'spawn';
    default:
      return step;
  }
}

function issuePhaseStatus(status: string): PhaseStatus {
  switch (status) {
    case 'started':
      return 'active';
    case 'ok':
      return 'ok';
    case 'failed':
      return 'failed';
    default:
      return 'pending';
  }
}

/**
 * A mutable working copy for one reduce call.
 *
 * A snapshot replays up to the agent's whole 500-event ring, and each event can
 * append a log line; rebuilding the 2000-entry array per event would make that
 * quadratic. Mutating a draft and materialising once keeps the exported reducer
 * pure without paying for it.
 */
interface Draft {
  instance?: AutoScanInstance;
  nextScanAt?: string;
  cycleNumber: number;
  activePhase?: string;
  phases: Record<PhaseKey, PhaseStatus>;
  recentIssues: IssueRow[];
  logLines: LogLine[];
  connection: DetailConnection;
  lastSeq: bigint;
  nextLogId: number;
  changed: boolean;
}

function toDraft(state: AutoScanDetailState): Draft {
  const draft: Draft = {
    cycleNumber: state.cycleNumber,
    phases: { ...state.phases },
    recentIssues: [...state.recentIssues],
    logLines: [...state.logLines],
    connection: state.connection,
    lastSeq: state.lastSeq,
    nextLogId: state.nextLogId,
    changed: false,
  };
  if (state.instance !== undefined) draft.instance = state.instance;
  if (state.nextScanAt !== undefined) draft.nextScanAt = state.nextScanAt;
  if (state.activePhase !== undefined) draft.activePhase = state.activePhase;
  return draft;
}

function fromDraft(state: AutoScanDetailState, draft: Draft): AutoScanDetailState {
  if (!draft.changed) return state;
  // Conditional key insertion rather than post-assignment: the state's fields
  // are `readonly`, and an absent optional has to stay absent so `undefined`
  // never has to be distinguished from "not set".
  return {
    projectName: state.projectName,
    cycleNumber: draft.cycleNumber,
    phases: draft.phases,
    recentIssues: draft.recentIssues,
    logLines: draft.logLines,
    connection: draft.connection,
    lastSeq: draft.lastSeq,
    nextLogId: draft.nextLogId,
    ...(draft.instance !== undefined && { instance: draft.instance }),
    ...(draft.nextScanAt !== undefined && { nextScanAt: draft.nextScanAt }),
    ...(draft.activePhase !== undefined && { activePhase: draft.activePhase }),
  };
}

function appendLog(draft: Draft, text: string, ansi = false): void {
  if (draft.logLines.length >= LOG_RING_MAX) draft.logLines.shift();
  draft.logLines.push({ id: draft.nextLogId, text, ansi });
  draft.nextLogId += 1;
  draft.changed = true;
}

function reducePhase(draft: Draft, payload: Extract<ScanPayload, { type: 'phase' }>): void {
  const cycle = payload.cycle_number;
  switch (payload.phase) {
    case 'scan_started':
      draft.cycleNumber = cycle;
      draft.activePhase = 'scan';
      // Every phase resets each cycle; `recentIssues` deliberately does not, so
      // an idle scan does not blank the list of what the last busy one did.
      draft.phases = freshPhases(true);
      appendLog(draft, `--- Scan #${String(cycle)} started ---`);
      break;
    case 'scan_completed': {
      const idle = payload.issues_processed === 0;
      draft.nextScanAt = payload.next_scan_at;
      draft.activePhase = undefined;
      for (const key of PHASE_ORDER) {
        const status = draft.phases[key];
        if (status === 'active') draft.phases[key] = 'ok';
        else if (idle && status === 'pending') draft.phases[key] = 'skipped';
      }
      appendLog(draft, `--- Scan #${String(cycle)} completed ---`);
      if (idle) appendLog(draft, 'no matching issues — skipping remaining stages');
      break;
    }
    case 'scan_stopped':
      draft.activePhase = undefined;
      appendLog(draft, '--- Scan stopped ---');
      break;
    default:
      break;
  }
}

function reduceIssue(draft: Draft, payload: Extract<ScanPayload, { type: 'issue' }>): void {
  const phaseKey = stepToPhase(payload.step);
  const phaseStatus = issuePhaseStatus(payload.status);
  // Only known phases are written. `step` is wire data, and iOS's fallback of
  // keying the map by the raw step would let a payload naming `__proto__` reach
  // `Object.prototype`'s setter — the hazard `autoscanFrames.ts` documents. The
  // timeline renders `PHASE_ORDER` only, so nothing visible is lost.
  if (PHASE_KEYS.has(phaseKey)) {
    draft.phases[phaseKey as PhaseKey] = phaseStatus;
    if (phaseStatus === 'active') draft.activePhase = phaseKey;
  }

  const row: IssueRow = {
    identifier: payload.identifier,
    title: payload.title,
    step: payload.step,
    status: payload.status,
  };
  if (payload.error !== undefined) row.error = payload.error;
  draft.recentIssues.push(row);
  if (draft.recentIssues.length > RECENT_ISSUES_MAX) draft.recentIssues.shift();

  const suffix = payload.error === undefined ? '' : ` [${payload.error}]`;
  appendLog(draft, `${payload.identifier} ${payload.step} ${payload.status}${suffix}`);
}

function reduceEvent(draft: Draft, event: AutoScanEvent): void {
  if (event.seq <= draft.lastSeq) return;
  draft.lastSeq = event.seq;
  draft.changed = true;

  const payload = parseScanPayload(event.type, event.payload);
  switch (payload.type) {
    case 'phase':
      reducePhase(draft, payload);
      break;
    case 'issue':
      reduceIssue(draft, payload);
      break;
    case 'worktree':
      appendLog(draft, `worktree: ${payload.path}`);
      break;
    case 'session': {
      const suffix = payload.pid === undefined ? '' : ` (pid=${String(payload.pid)})`;
      appendLog(draft, `session: ${payload.session_id}${suffix}`);
      break;
    }
    case 'log':
      appendLog(draft, payload.line, payload.ansi);
      break;
    default:
      break;
  }
}

function reduceFrame(draft: Draft, state: AutoScanDetailState, frame: DetailFrame): void {
  switch (frame.kind) {
    case 'connected':
      // Not `connected` yet: the subscribe frame has gone out but the snapshot
      // that answers it has not arrived, so the log on screen is still whatever
      // survived the drop.
      draft.connection = 'connecting';
      draft.changed = true;
      break;
    case 'disconnected':
      draft.connection = 'disconnected';
      draft.changed = true;
      break;
    case 'snapshot': {
      // `scans` is `unknown[]` on the frame — the wire layer deliberately stops
      // short of modelling it — so each row is validated here rather than cast.
      let match: AutoScanInstance | undefined;
      for (const raw of frame.scans) {
        const decoded = autoScanInstanceSchema.safeParse(raw);
        if (!decoded.success || decoded.data.project_name !== state.projectName) continue;
        match = decoded.data;
        break;
      }
      // Assigned unconditionally, as iOS does: a snapshot that no longer carries
      // this project means the scan is gone, and holding the last row would leave
      // a stopped scan rendering "Scanning" and a frozen count for the life of
      // the screen. `cycleNumber` is the exception — it is also driven by phase
      // events, so a missing row must not reset it.
      draft.instance = match;
      draft.nextScanAt = match?.next_scan_at;
      if (match !== undefined) draft.cycleNumber = match.cycle_number;

      draft.connection = 'replaying';
      draft.changed = true;
      // `Object.hasOwn`, not a bare index: project names come off the wire and
      // ride in the URL, and `events['__proto__' | 'toString' | 'constructor']`
      // resolves to a *non-nullish* inherited value — so `?? []` never fires and
      // the `for…of` throws out of a React render. `autoscanFrames.ts` hardened
      // the assignment half of this hazard; this is the lookup half.
      const buffered = Object.hasOwn(frame.events, state.projectName)
        ? frame.events[state.projectName]
        : undefined;
      for (const event of buffered ?? []) reduceEvent(draft, event);
      break;
    }
    case 'caughtUp':
      draft.connection = 'connected';
      draft.changed = true;
      break;
    case 'event':
      // The channel can carry several projects at once; the detail screen is one.
      if (frame.event.projectName !== state.projectName) return;
      reduceEvent(draft, frame.event);
      break;
    default:
      break;
  }
}

/** Reduce a batch of frames in arrival order. Returns `state` itself if nothing changed. */
export function reduceAutoScanFrames(
  state: AutoScanDetailState,
  frames: readonly DetailFrame[],
): AutoScanDetailState {
  if (frames.length === 0) return state;
  const draft = toDraft(state);
  for (const frame of frames) reduceFrame(draft, state, frame);
  return fromDraft(state, draft);
}

export function reduceAutoScanFrame(
  state: AutoScanDetailState,
  frame: DetailFrame,
): AutoScanDetailState {
  return reduceAutoScanFrames(state, [frame]);
}

/** The timeline as it renders: fixed order, every key present. */
export function orderedPhases(
  state: AutoScanDetailState,
): readonly { key: PhaseKey; status: PhaseStatus }[] {
  return PHASE_ORDER.map((key) => ({ key, status: state.phases[key] }));
}

/**
 * What a reconnect should ask the agent to resume from.
 *
 * Rebuilt from the live cursor on every call rather than captured once — that
 * capture is half of the Android bug this port exists to avoid.
 */
export function resumeFromState(state: AutoScanDetailState): Record<string, bigint> {
  return state.lastSeq > 0n ? { [state.projectName]: state.lastSeq } : {};
}
