import { describe, expect, it } from 'vitest';
import type { CustomAgentType } from '../../../api/customAgentTypeRepository';
import type { ProjectSummary } from '../../../api/projectRepository';
import { agentVersionSchema, type AgentVersionInfo } from '../../../protocol/dto/agentVersion';
import { applyModeOrder, buildModeCards, type ModeCard } from './modeCatalog';

const capabilities = (cli: Record<string, unknown>): AgentVersionInfo =>
  agentVersionSchema.parse({
    name: 'mobaide-agent',
    server_version: '1.4.0',
    protocol_versions: [7],
    min_client_version: 1,
    capabilities: { cli },
  });

const project = (over: Partial<ProjectSummary> = {}): ProjectSummary => ({
  name: 'mobaide',
  path: '/srv/mobaide',
  order: 0,
  has_linear_key: true,
  is_git: true,
  path_exists: true,
  auto_scan_prompt: undefined,
  ...over,
});

const cards = (input: {
  capabilities?: AgentVersionInfo;
  project?: ProjectSummary | null;
  customTypes?: CustomAgentType[];
}): ModeCard[] =>
  buildModeCards({
    capabilities: input.capabilities,
    project: input.project === undefined ? project() : input.project,
    customTypes: input.customTypes ?? [],
  });

const card = (list: ModeCard[], mode: string): ModeCard => {
  const found = list.find((entry) => entry.mode === mode);
  if (found === undefined) throw new Error(`no card for ${mode}`);
  return found;
};

describe('buildModeCards', () => {
  it('offers the seven built-ins in order, then the account’s custom types', () => {
    const list = cards({
      customTypes: [
        {
          id: '0123456789ab',
          name: 'My Linter',
          command: 'npm run lint',
          createdAt: '2026-08-04T10:00:00Z',
          updatedAt: '2026-08-04T10:00:00Z',
        },
      ],
    });

    expect(list.map((entry) => entry.mode)).toEqual([
      'claude',
      'codex',
      'antigravity',
      'opencode',
      'copilot',
      'auto_scan',
      'shell',
      'custom_0123456789ab',
    ]);
    expect(card(list, 'custom_0123456789ab')).toEqual({
      mode: 'custom_0123456789ab',
      title: 'My Linter',
      subtitle: 'npm run lint',
      command: 'npm run lint',
      enabled: true,
    });
  });

  it('leaves every mode launchable when the agent reports nothing', () => {
    // An absent probe, an unreachable agent or a reshaped capability block all
    // read as unknown, and greying out a mode that would have worked is worse
    // than surfacing the agent's own error.
    const list = cards({ capabilities: undefined });

    expect(list.filter((entry) => !entry.enabled)).toEqual([]);
  });

  it('disables a CLI only on an explicit false, with the mobile copy', () => {
    const list = cards({
      capabilities: capabilities({
        claude: { available: false },
        codex: { available: false },
        antigravity: { available: false },
      }),
    });

    expect(card(list, 'claude').disabledReason).toBe(
      'Claude CLI is not installed on the agent.',
    );
    expect(card(list, 'codex').disabledReason).toBe('Codex CLI is not installed on the agent.');
    expect(card(list, 'antigravity').disabledReason).toBe(
      'Antigravity CLI (agy) is not installed on the agent.',
    );
    expect(card(list, 'shell').enabled).toBe(true);
  });

  it('disables GitHub Copilot on an agent from before Copilot support', () => {
    // That agent would open a plain shell for `cli: "copilot"`.
    const older = cards({
      capabilities: capabilities({ claude: { available: true }, codex: { available: true } }),
    });
    const missing = cards({ capabilities: capabilities({ copilot: { available: false } }) });
    const installed = cards({ capabilities: capabilities({ copilot: { available: true } }) });

    expect(card(older, 'copilot')).toMatchObject({
      enabled: false,
      disabledReason: 'Update Mobaide on your PC to use GitHub Copilot.',
    });
    expect(card(older, 'codex').enabled).toBe(true);
    expect(card(missing, 'copilot')).toMatchObject({
      enabled: false,
      disabledReason: 'GitHub Copilot CLI is not installed on the agent.',
    });
    expect(card(installed, 'copilot')).toMatchObject({ enabled: true, disabledReason: undefined });
    expect(card(cards({ capabilities: capabilities({}) }), 'copilot').enabled).toBe(true);
  });

  it('gates Auto Scan on both the Claude CLI and a Linear key', () => {
    const missingKey = cards({ project: project({ has_linear_key: false }) });
    const missingClaude = cards({ capabilities: capabilities({ claude: { available: false } }) });
    const missingBoth = cards({
      capabilities: capabilities({ claude: { available: false } }),
      project: null,
    });

    expect(card(cards({}), 'auto_scan')).toMatchObject({
      enabled: true,
      disabledReason: undefined,
    });
    expect(card(missingKey, 'auto_scan').disabledReason).toBe(
      'Auto Scan needs a Linear API key for this project.',
    );
    // The missing CLI is the more actionable of the two, so it wins.
    expect(card(missingClaude, 'auto_scan').disabledReason).toBe(
      "Auto Scan needs the Claude CLI, which isn't installed on the agent.",
    );
    expect(card(missingBoth, 'auto_scan').disabledReason).toBe(
      "Auto Scan needs the Claude CLI, which isn't installed on the agent.",
    );
  });
});

describe('buildModeCards — OpenCode', () => {
  it('asks for an agent update where the agent lists its CLIs without OpenCode', () => {
    // That agent would open a plain shell for `cli: "opencode"`.
    const list = cards({
      capabilities: capabilities({ claude: { available: true }, codex: { available: true } }),
    });

    expect(card(list, 'opencode').enabled).toBe(false);
    expect(card(list, 'opencode').disabledReason).toBe(
      'Update Mobaide on your PC to use OpenCode.',
    );
    expect(card(list, 'claude').enabled).toBe(true);
  });

  it('says the CLI is missing where the agent knows OpenCode but did not find it', () => {
    const list = cards({ capabilities: capabilities({ opencode: { available: false } }) });

    expect(card(list, 'opencode').enabled).toBe(false);
    expect(card(list, 'opencode').disabledReason).toBe(
      'OpenCode CLI is not installed on the agent.',
    );
  });

  it.each<[string, AgentVersionInfo | undefined]>([
    ['the agent found the CLI', capabilities({ opencode: { available: true } })],
    ['the probe is still out', undefined],
    ['the agent reports no CLI list', capabilities({})],
  ])('is launchable when %s', (_name, version) => {
    const opencode = card(cards({ capabilities: version }), 'opencode');

    expect(opencode.enabled).toBe(true);
    expect(opencode.disabledReason).toBeUndefined();
  });
});

describe('applyModeOrder', () => {
  const modes = (list: readonly { mode: string }[]): string[] => list.map((entry) => entry.mode);
  const defaults = [
    { mode: 'claude' },
    { mode: 'codex' },
    { mode: 'antigravity' },
    { mode: 'auto_scan' },
    { mode: 'shell' },
    { mode: 'custom_aaaaaaaaaaaa' },
    { mode: 'custom_bbbbbbbbbbbb' },
  ];

  it('ranks every card the saved order names by its place there', () => {
    const order = [
      'custom_bbbbbbbbbbbb',
      'shell',
      'claude',
      'auto_scan',
      'codex',
      'custom_aaaaaaaaaaaa',
      'antigravity',
    ];

    expect(modes(applyModeOrder(defaults, order))).toEqual(order);
  });

  it('puts cards the order does not name after the named ones, in default order', () => {
    // A custom type created after the last reorder, or a built-in a newer
    // client added: neither may jump ahead of what the user arranged.
    expect(modes(applyModeOrder(defaults, ['shell', 'codex']))).toEqual([
      'shell',
      'codex',
      'claude',
      'antigravity',
      'auto_scan',
      'custom_aaaaaaaaaaaa',
      'custom_bbbbbbbbbbbb',
    ]);
  });

  it('skips ids that match no card, such as a deleted custom type', () => {
    expect(
      modes(applyModeOrder(defaults.slice(0, 3), ['custom_gone00000000', 'codex', 'claude'])),
    ).toEqual(['codex', 'claude', 'antigravity']);
  });

});
