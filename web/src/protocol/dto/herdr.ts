/**
 * The Herdr dashboard records (`GET /api/herdr`, the `herdr.changed` agent
 * event) and the `herdr.attention` alert.
 *
 * Mobaide owns these shapes: the agent turns the records of both herdr
 * products into them, so nothing here names a herdr method. `status`, `agent`,
 * `product` and `state` stay open strings — a newer agent may send a value
 * this client does not know, and it must still show the pane.
 */
import { z } from 'zod';
import { optionalWireNumber, optionalWireString } from '../wireSchema';

export const herdrRectSchema = z.looseObject({
  x: z.number(),
  y: z.number(),
  width: z.number(),
  height: z.number(),
});

export const herdrPaneSchema = z.looseObject({
  pane_id: z.string(),
  title: z.string().default(''),
  /** `null` on the wire for a pane without an agent. */
  agent: optionalWireString(),
  status: optionalWireString(),
  /** Changes each time the agent finishes a turn; see `herdrSeenStore`. */
  turn: optionalWireNumber(),
  cwd: optionalWireString(),
  agent_session_id: optionalWireString(),
  rect: herdrRectSchema.nullish().transform((value) => value ?? undefined),
});

export const herdrTabSchema = z.looseObject({
  tab_id: z.string(),
  label: z.string().default(''),
  width: optionalWireNumber(),
  height: optionalWireNumber(),
  panes: z.array(herdrPaneSchema).default([]),
});

export const herdrWorkspaceSchema = z.looseObject({
  workspace_id: z.string(),
  label: z.string().default(''),
  tabs: z.array(herdrTabSchema).default([]),
});

export const herdrAgentKindSchema = z.looseObject({
  id: z.string(),
  label: z.string(),
});

/** Every flag defaults to off: an action is offered only on an explicit `true`. */
export const herdrFeaturesSchema = z.looseObject({
  new_workspace: z.boolean().default(false),
  new_tab: z.boolean().default(false),
  split: z.boolean().default(false),
  rename: z.boolean().default(false),
  close_group: z.boolean().default(false),
  close_pane: z.boolean().default(false),
  control: z.boolean().default(false),
  type_in_watch: z.boolean().default(false),
  agents: z.array(herdrAgentKindSchema).default([]),
});

export const herdrServerSchema = z.looseObject({
  server_id: z.string(),
  /** `tui` (Herdr TUI) or `core` (herdr-core). */
  product: z.string().default('tui'),
  name: z.string().default('default'),
  version: optionalWireString(),
  /** `ready` or `outdated`. */
  state: z.string().default('ready'),
  message: optionalWireString(),
  features: herdrFeaturesSchema.default(() => herdrFeaturesSchema.parse({})),
  workspaces: z.array(herdrWorkspaceSchema).default([]),
});

export const herdrSnapshotSchema = z.looseObject({
  servers: z.array(herdrServerSchema).default([]),
});

export type HerdrPane = z.infer<typeof herdrPaneSchema>;
export type HerdrTab = z.infer<typeof herdrTabSchema>;
export type HerdrWorkspace = z.infer<typeof herdrWorkspaceSchema>;
export type HerdrFeatures = z.infer<typeof herdrFeaturesSchema>;
export type HerdrAgentKind = z.infer<typeof herdrAgentKindSchema>;
export type HerdrServer = z.infer<typeof herdrServerSchema>;
export type HerdrSnapshot = z.infer<typeof herdrSnapshotSchema>;

export const HERDR_SERVER_READY = 'ready';

/**
 * `herdr.attention`: an agent in a pane needs the user. On the user-events
 * channel the server adds `agent_id`; on the agent channel it is absent.
 */
export const herdrAttentionSchema = z.looseObject({
  server_id: z.string(),
  pane_id: z.string(),
  reason: z.string(),
  title: z.string(),
  agent: optionalWireString(),
  agent_id: optionalWireString(),
});

export type HerdrAttention = z.infer<typeof herdrAttentionSchema>;
