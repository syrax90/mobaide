/**
 * The chat-session wire contract on the per-session socket, pinned by
 * `tests/protocol_fixtures/frames/chat_frames.json`.
 *
 * | Byte | Dir   | Payload                                   |
 * |------|-------|-------------------------------------------|
 * | 'c'  | C→S   | one UTF-8 JSON command                    |
 * | 'a'  | S→C   | one UTF-8 JSON frame                      |
 * | 's'  | S→C   | the PTY state frame (`stopped` = gone)    |
 *
 * Inbound data is normalised into view types here, once, so nothing above
 * this module re-checks wire shapes. Anything unknown degrades instead of
 * failing: an unknown item or content part is dropped, an unknown event still
 * advances `seq`, an unknown tool kind renders as `other`.
 */
import { z } from 'zod';
import { decodeTerminalState } from '../protocol/dto/terminalState';

export const CHAT_FRAME_TYPE = {
  command: 0x63,
  frame: 0x61,
  state: 0x73,
} as const;

const KNOWN_STATUSES = ['starting', 'loading', 'ready', 'running', 'error', 'stopped'] as const;
export type ChatStatus = (typeof KNOWN_STATUSES)[number];

export const TOOL_KINDS = [
  'read',
  'edit',
  'delete',
  'move',
  'search',
  'execute',
  'think',
  'fetch',
  'switch_mode',
  'other',
] as const;
export type ToolKind = (typeof TOOL_KINDS)[number];

const TOOL_STATUSES = ['pending', 'in_progress', 'completed', 'failed', 'cancelled'] as const;
export type ToolStatus = (typeof TOOL_STATUSES)[number];

function oneOf<T extends string>(values: readonly T[], fallback: T) {
  return z
    .unknown()
    .transform((value): T =>
      typeof value === 'string' && (values as readonly string[]).includes(value)
        ? (value as T)
        : fallback,
    );
}

const text = z.string().catch('');
const nullableText = z.string().nullable().catch(null);
const flag = z.boolean().catch(false);
const count = z.number().catch(0);

/** Keeps the entries that parse and drops the rest, rather than failing the list. */
function lenientArray<T>(item: z.ZodType<T>) {
  return z
    .array(z.unknown())
    .catch([])
    .transform((list) => {
      const out: T[] = [];
      for (const entry of list) {
        const parsed = item.safeParse(entry);
        if (parsed.success) out.push(parsed.data);
      }
      return out;
    });
}

// ---------- content parts ----------

const textPartSchema = z.object({
  type: z.literal('text'),
  text,
  truncated: flag,
});
const diffPartSchema = z.object({
  type: z.literal('diff'),
  path: text,
  new_file: flag,
  diff: text,
  added: count,
  removed: count,
  truncated: flag,
});
const imagePartSchema = z.object({
  type: z.literal('image'),
  mime: text,
  data: z.string().nullable().optional().catch(null),
});
const linkPartSchema = z.object({ type: z.literal('link'), uri: text, name: text });
const resourcePartSchema = z.object({
  type: z.literal('resource'),
  uri: text,
  name: text,
  text: z.string().nullable().optional().catch(null),
});
const terminalPartSchema = z.object({ type: z.literal('terminal'), terminal_id: text });

const contentPartSchema = z.discriminatedUnion('type', [
  textPartSchema,
  diffPartSchema,
  imagePartSchema,
  linkPartSchema,
  resourcePartSchema,
  terminalPartSchema,
]);

export type ContentPart = z.infer<typeof contentPartSchema>;
export type DiffPart = z.infer<typeof diffPartSchema>;

const contentPartsSchema = lenientArray(contentPartSchema);

// ---------- items ----------

const attachmentSchema = z.object({
  kind: text,
  mime: z.string().optional().catch(undefined),
  name: z.string().optional().catch(undefined),
});

const userItemSchema = z.object({
  id: z.string(),
  type: z.literal('user'),
  text,
  attachments: lenientArray(attachmentSchema),
  at: z.string().nullable().optional().catch(null),
});
const agentItemSchema = z.object({ id: z.string(), type: z.literal('agent'), text });
const thoughtItemSchema = z.object({ id: z.string(), type: z.literal('thought'), text });
const toolItemSchema = z.object({
  id: z.string(),
  type: z.literal('tool'),
  title: text,
  kind: oneOf(TOOL_KINDS, 'other'),
  status: oneOf(TOOL_STATUSES, 'pending'),
  subtitle: nullableText,
  locations: lenientArray(
    z.object({ path: z.string(), line: z.number().nullable().optional().catch(null) }),
  ),
  content: contentPartsSchema,
  input: nullableText,
});
const noticeItemSchema = z.object({
  id: z.string(),
  type: z.literal('notice'),
  severity: text,
  title: text,
  text,
});
const compactionItemSchema = z.object({
  id: z.string(),
  type: z.literal('compaction'),
  status: text,
  text,
});
const turnEndItemSchema = z.object({
  id: z.string(),
  type: z.literal('turn_end'),
  stop_reason: text,
  text,
});
const errorItemSchema = z.object({ id: z.string(), type: z.literal('error'), text });

const itemSchema = z.discriminatedUnion('type', [
  userItemSchema,
  agentItemSchema,
  thoughtItemSchema,
  toolItemSchema,
  noticeItemSchema,
  compactionItemSchema,
  turnEndItemSchema,
  errorItemSchema,
]);

export type ChatItem = z.infer<typeof itemSchema>;
export type UserItem = z.infer<typeof userItemSchema>;
export type ToolItem = z.infer<typeof toolItemSchema>;
export type NoticeItem = z.infer<typeof noticeItemSchema>;
export type CompactionItem = z.infer<typeof compactionItemSchema>;

/** `null` for an unknown item type, which the timeline skips. */
export function parseChatItem(raw: unknown): ChatItem | null {
  const parsed = itemSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

// ---------- state ----------

const configChoiceSchema = z.object({
  value: z.string(),
  name: text,
  description: nullableText,
  group: nullableText,
});
const configBase = {
  id: z.string(),
  name: text,
  description: nullableText,
  category: nullableText,
};
const configOptionSchema = z.discriminatedUnion('type', [
  z.object({
    ...configBase,
    type: z.literal('select'),
    value: text,
    options: lenientArray(configChoiceSchema),
  }),
  z.object({ ...configBase, type: z.literal('boolean'), value: flag }),
]);
export type ChatConfigOption = z.infer<typeof configOptionSchema>;
export type ChatSelectOption = Extract<ChatConfigOption, { type: 'select' }>;
export type ChatConfigChoice = z.infer<typeof configChoiceSchema>;

const commandInfoSchema = z.object({ name: z.string(), description: text, hint: nullableText });
export type ChatCommandInfo = z.infer<typeof commandInfoSchema>;

const usageSchema = z
  .object({
    used: z.number(),
    size: z.number(),
    cost: z.object({ amount: z.number(), currency: text }).nullable().optional().catch(null),
  })
  .nullable()
  .catch(null);
export type ChatUsage = NonNullable<z.infer<typeof usageSchema>>;

const planEntrySchema = z.object({
  content: text,
  status: oneOf(['pending', 'in_progress', 'completed'] as const, 'pending'),
  priority: text,
});
export type ChatPlanEntry = z.infer<typeof planEntrySchema>;

const permissionOptionSchema = z.object({ id: z.string(), name: text, kind: text });
export type PermissionOption = z.infer<typeof permissionOptionSchema>;

const FORM_FIELD_TYPES = ['select', 'multiselect', 'text', 'boolean', 'number', 'integer'] as const;
const formFieldSchema = z.object({
  key: z.string(),
  title: text,
  description: nullableText,
  required: flag,
  type: oneOf(FORM_FIELD_TYPES, 'text'),
  options: lenientArray(
    z.object({ value: z.string(), label: text, description: nullableText }),
  ),
  default: z.unknown().optional(),
});
export type FormField = z.infer<typeof formFieldSchema>;

const permissionRequestSchema = z.object({
  id: z.string(),
  kind: z.literal('permission'),
  tool_call_id: nullableText,
  title: text,
  tool_kind: oneOf(TOOL_KINDS, 'other'),
  subtitle: nullableText,
  content: contentPartsSchema,
  options: lenientArray(permissionOptionSchema),
});
const formRequestSchema = z.object({
  id: z.string(),
  kind: z.literal('form'),
  tool_call_id: nullableText,
  title: text,
  fields: lenientArray(formFieldSchema),
});
const signInMethodSchema = z.object({ id: z.string(), name: text, description: text });
export type SignInMethod = z.infer<typeof signInMethodSchema>;
/** The PC agent runs the agent's own login and shows what the user needs from it. */
const signInRequestSchema = z.object({
  id: z.string(),
  kind: z.literal('sign_in'),
  tool_call_id: nullableText,
  title: text,
  text,
  stage: oneOf(['choose', 'waiting'] as const, 'choose'),
  methods: lenientArray(signInMethodSchema),
  url: nullableText,
  user_code: nullableText,
  input: z.object({ label: text, secret: flag }).nullable().catch(null),
  busy: flag,
  error: nullableText,
});
const unknownRequestSchema = z.object({
  id: z.string(),
  kind: z.string().transform((): 'unknown' => 'unknown'),
  tool_call_id: nullableText,
  title: text,
});
const requestSchema = z.union([
  permissionRequestSchema,
  formRequestSchema,
  signInRequestSchema,
  unknownRequestSchema,
]);
export type ChatRequest = z.infer<typeof requestSchema>;
export type PermissionRequest = z.infer<typeof permissionRequestSchema>;
export type FormRequest = z.infer<typeof formRequestSchema>;
export type SignInRequest = z.infer<typeof signInRequestSchema>;

const queuedSchema = z.object({ id: z.string(), text, images: count });
export type ChatQueued = z.infer<typeof queuedSchema>;

const agentInfoSchema = z
  .object({ name: text, title: text, version: text })
  .nullable()
  .catch(null);

const STATE_FIELDS = {
  status: oneOf(KNOWN_STATUSES, 'ready'),
  error: nullableText,
  agent: agentInfoSchema,
  title: nullableText,
  cwd: nullableText,
  acp_session_id: nullableText,
  config: lenientArray(configOptionSchema),
  commands: lenientArray(commandInfoSchema),
  usage: usageSchema,
  plan: lenientArray(planEntrySchema),
  requests: lenientArray(requestSchema),
  queue: lenientArray(queuedSchema),
  prompt_caps: z.object({ image: flag }).catch({ image: false }),
  turn_started_at: nullableText,
  cancelling: flag,
  steering: flag,
};

type StateFields = typeof STATE_FIELDS;
export type ChatState = { [K in keyof StateFields]: z.infer<StateFields[K]> };

export const INITIAL_CHAT_STATE: ChatState = {
  status: 'starting',
  error: null,
  agent: null,
  title: null,
  cwd: null,
  acp_session_id: null,
  config: [],
  commands: [],
  usage: null,
  plan: [],
  requests: [],
  queue: [],
  prompt_caps: { image: false },
  turn_started_at: null,
  cancelling: false,
  steering: false,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Only the known keys present in `patch`, each normalised; unknown keys are ignored. */
export function normalizeStatePatch(patch: unknown): Partial<ChatState> {
  if (!isRecord(patch)) return {};
  const out: Partial<Record<keyof StateFields, unknown>> = {};
  for (const key of Object.keys(STATE_FIELDS) as (keyof StateFields)[]) {
    if (Object.hasOwn(patch, key)) out[key] = STATE_FIELDS[key].parse(patch[key]);
  }
  // Each value came out of the schema for its own key.
  return out as Partial<ChatState>;
}

export function normalizeState(raw: unknown): ChatState {
  return { ...INITIAL_CHAT_STATE, ...normalizeStatePatch(raw) };
}

// ---------- frames ----------

export type ChatEvent =
  | { type: 'item'; seq: number; item: ChatItem | null }
  | { type: 'append'; seq: number; id: string; text: string }
  | { type: 'remove'; seq: number; id: string }
  | { type: 'state'; seq: number; patch: Partial<ChatState> }
  | { type: 'skip'; seq: number };

export interface SnapshotFrame {
  t: 'snapshot';
  epoch: string;
  seq: number;
  truncated: boolean;
  state: ChatState;
  items: ChatItem[];
}

export interface EventsFrame {
  t: 'events';
  epoch: string;
  seq: number;
  events: ChatEvent[];
}

export interface ResultFrame {
  t: 'result';
  id: string;
  ok: boolean;
  data: unknown;
  error: string | null;
}

export type ChatFrame = SnapshotFrame | EventsFrame | ResultFrame;

function isSeq(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function parseEvent(raw: unknown): ChatEvent | null {
  if (!isRecord(raw) || !isSeq(raw.seq)) return null;
  const seq = raw.seq;
  switch (raw.type) {
    case 'item':
      return { type: 'item', seq, item: parseChatItem(raw.item) };
    case 'append':
      return typeof raw.id === 'string' && typeof raw.text === 'string'
        ? { type: 'append', seq, id: raw.id, text: raw.text }
        : { type: 'skip', seq };
    case 'remove':
      return typeof raw.id === 'string' ? { type: 'remove', seq, id: raw.id } : { type: 'skip', seq };
    case 'state':
      return { type: 'state', seq, patch: normalizeStatePatch(raw.patch) };
    default:
      return { type: 'skip', seq };
  }
}

/** One `'a'` frame body, or `null` when it is not a frame this client knows. */
export function parseChatFrame(raw: unknown): ChatFrame | null {
  if (!isRecord(raw)) return null;
  switch (raw.t) {
    case 'snapshot': {
      if (typeof raw.epoch !== 'string' || !isSeq(raw.seq)) return null;
      const items = Array.isArray(raw.items) ? raw.items : [];
      return {
        t: 'snapshot',
        epoch: raw.epoch,
        seq: raw.seq,
        truncated: raw.truncated === true,
        state: normalizeState(raw.state),
        items: items.map(parseChatItem).filter((item): item is ChatItem => item !== null),
      };
    }
    case 'events': {
      if (typeof raw.epoch !== 'string' || !isSeq(raw.seq)) return null;
      const events = Array.isArray(raw.events) ? raw.events : [];
      return {
        t: 'events',
        epoch: raw.epoch,
        seq: raw.seq,
        events: events.map(parseEvent).filter((event): event is ChatEvent => event !== null),
      };
    }
    case 'result':
      if (typeof raw.id !== 'string') return null;
      return {
        t: 'result',
        id: raw.id,
        ok: raw.ok === true,
        data: raw.data,
        error: typeof raw.error === 'string' ? raw.error : null,
      };
    default:
      return null;
  }
}

// ---------- commands ----------

export interface ChatImage {
  mime: string;
  /** Base64 without a `data:` prefix. */
  data: string;
}

export type FormValue = string | string[] | boolean | number;

export type ChatCommand =
  | { op: 'sync'; epoch?: string; after_seq?: number }
  | { op: 'prompt'; text: string; images?: ChatImage[] }
  | { op: 'cancel' }
  | { op: 'set_config'; config_id: string; value: string | boolean }
  | { op: 'respond'; request_id: string; option_id: string }
  | {
      op: 'respond';
      request_id: string;
      action: 'accept' | 'decline' | 'cancel';
      values?: Record<string, FormValue>;
    }
  | { op: 'dequeue'; queue_id: string }
  | { op: 'steer'; queue_id: string }
  | { op: 'restart' }
  | { op: 'sign_in'; action: 'start'; method: string }
  | { op: 'sign_in'; action: 'submit'; value: string }
  | { op: 'sign_in'; action: 'cancel' };

export function syncCommand(resume: { epoch: string; seq: number } | null): ChatCommand {
  return resume === null
    ? { op: 'sync' }
    : { op: 'sync', epoch: resume.epoch, after_seq: resume.seq };
}

export function promptCommand(text: string, images: readonly ChatImage[]): ChatCommand {
  return images.length === 0 ? { op: 'prompt', text } : { op: 'prompt', text, images: [...images] };
}

export function formResponseCommand(
  requestId: string,
  action: 'accept' | 'decline' | 'cancel',
  values?: Record<string, FormValue>,
): ChatCommand {
  return action === 'accept'
    ? { op: 'respond', request_id: requestId, action, values: values ?? {} }
    : { op: 'respond', request_id: requestId, action };
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();

export function encodeChatCommand(id: string, command: ChatCommand): Uint8Array {
  const json = encoder.encode(JSON.stringify({ id, ...command }));
  const out = new Uint8Array(json.length + 1);
  out[0] = CHAT_FRAME_TYPE.command;
  out.set(json, 1);
  return out;
}

export type ChatInbound =
  | { kind: 'frame'; frame: ChatFrame }
  | { kind: 'state'; status: string | null };

/**
 * Decode one binary message. The PTY replay frames (`'b'`, `'o'`, `'e'`) the
 * agent still sends for older clients, and anything else, decode to `null`.
 */
export function decodeChatInbound(bytes: Uint8Array): ChatInbound | null {
  const type = bytes[0];
  if (type === CHAT_FRAME_TYPE.frame) {
    let raw: unknown;
    try {
      raw = JSON.parse(decoder.decode(bytes.subarray(1)));
    } catch {
      return null;
    }
    const frame = parseChatFrame(raw);
    return frame === null ? null : { kind: 'frame', frame };
  }
  if (type === CHAT_FRAME_TYPE.state) {
    return { kind: 'state', status: decodeTerminalState(decoder.decode(bytes.subarray(1)))?.status ?? null };
  }
  return null;
}
