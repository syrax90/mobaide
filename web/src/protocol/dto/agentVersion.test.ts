import { describe, expect, it } from 'vitest';
import {
  NO_GIT_CAPABILITIES,
  agentVersionSchema,
  cliAvailable,
  cliModelDetails,
  cliModels,
  cliModelsPending,
  gitCapabilities,
  gitCapability,
  hasGitSync,
  type AgentVersionInfo,
} from './agentVersion';

const info = (capabilities: unknown): AgentVersionInfo =>
  agentVersionSchema.parse({
    name: 'mobaide-agent',
    server_version: '1.4.0',
    protocol_versions: [7],
    min_client_version: 1,
    capabilities,
  });

describe('agentVersionSchema', () => {
  it('materialises absent capabilities as an empty block', () => {
    expect(info(null).capabilities).toEqual({});
    expect(agentVersionSchema.safeParse({ name: 'x' }).success).toBe(false);
  });
});

describe('cliAvailable', () => {
  it('reports the boolean the agent sent', () => {
    const probe = info({ cli: { claude: { available: true }, codex: { available: false } } });

    expect(cliAvailable(probe, 'claude')).toBe(true);
    expect(cliAvailable(probe, 'codex')).toBe(false);
  });

  it('tolerates the stringified form iOS accepts', () => {
    const probe = info({ cli: { claude: { available: 'true' }, codex: { available: 'false' } } });

    expect(cliAvailable(probe, 'claude')).toBe(true);
    expect(cliAvailable(probe, 'codex')).toBe(false);
  });

  const UNKNOWN: [string, unknown][] = [
    ['no probe at all', undefined],
    ['no capabilities', {}],
    ['a cli block of the wrong shape', { cli: 'yes' }],
    ['an unlisted cli', { cli: { codex: { available: true } }, extra: 1 }],
    ['an unreadable available field', { cli: { claude: { available: 1 } } }],
    ['no available field', { cli: { claude: { models: ['opus'] } } }],
  ];

  it.each(UNKNOWN)('answers unknown for %s, which callers treat as enabled', (_label, capabilities) => {
    const probe = capabilities === undefined ? undefined : info(capabilities);

    expect(cliAvailable(probe, 'claude')).toBeNull();
  });

  it('does not mistake an inherited object key for a cli entry', () => {
    // `cliId` reaches here from a URL search param.
    expect(cliAvailable(info({ cli: {} }), 'constructor')).toBeNull();
  });
});

describe('cliModels', () => {
  it('returns the advertised names, coercing the numeric ones', () => {
    const probe = info({ cli: { claude: { models: ['opus', 4.6, '', 'haiku'] } } });

    expect(cliModels(probe, 'claude')).toEqual(['opus', '4.6', 'haiku']);
  });

  it('falls back to the hardcoded list when the agent knows of none', () => {
    // An empty picker is worse than the client's own defaults.
    expect(cliModels(info({ cli: { claude: { models: [] } } }), 'claude')).toBeNull();
    expect(cliModels(info({ cli: { claude: { models: ['', null] } } }), 'claude')).toBeNull();
    expect(cliModels(info({ cli: { claude: { models: 'opus' } } }), 'claude')).toBeNull();
    expect(cliModels(info({ cli: { claude: { available: true } } }), 'claude')).toBeNull();
    expect(cliModels(undefined, 'claude')).toBeNull();
  });
});

describe('cliModelsPending', () => {
  it('reads the warming marker, defaulting to false for anything else', () => {
    const probe = info({
      cli: { codex: { available: true, models_pending: true }, claude: { available: true } },
    });

    expect(cliModelsPending(probe, 'codex')).toBe(true);
    // Absent key (old agent / finished warmup), unreadable shapes, and a
    // missing probe all read as "not pending" — the marker is opt-in.
    expect(cliModelsPending(probe, 'claude')).toBe(false);
    expect(cliModelsPending(info({ cli: { codex: { models_pending: 'yes' } } }), 'codex')).toBe(
      false,
    );
    expect(cliModelsPending(info({}), 'codex')).toBe(false);
    expect(cliModelsPending(undefined, 'codex')).toBe(false);
  });
});

describe('cliModelDetails', () => {
  it('parses the advertised per-model effort sets', () => {
    const probe = info({
      cli: {
        codex: {
          model_details: [
            { id: 'gpt-5.6-sol', efforts: ['low', 'high', 'ultra'], default_effort: 'low' },
            { id: 'gpt-5.4-mini', efforts: ['low'] },
          ],
        },
      },
    });

    expect(cliModelDetails(probe, 'codex')).toEqual([
      { id: 'gpt-5.6-sol', efforts: ['low', 'high', 'ultra'], defaultEffort: 'low' },
      { id: 'gpt-5.4-mini', efforts: ['low'], defaultEffort: null },
    ]);
  });

  it('drops malformed entries and salvages the rest', () => {
    const probe = info({
      cli: {
        codex: {
          model_details: [
            'not an object',
            { id: '', efforts: ['low'] },
            { id: 'no-efforts' },
            { id: 'empty-efforts', efforts: [] },
            { id: 'non-string-efforts', efforts: [1, ''] },
            { id: 'ok', efforts: ['medium'], default_effort: '' },
          ],
        },
      },
    });

    expect(cliModelDetails(probe, 'codex')).toEqual([
      { id: 'ok', efforts: ['medium'], defaultEffort: null },
    ]);
  });

  it('answers null when nothing usable was advertised', () => {
    // Same posture as cliModels: absent, empty, or unreadable all mean
    // "use the client's hardcoded effort list".
    expect(cliModelDetails(info({ cli: { codex: { model_details: [] } } }), 'codex')).toBeNull();
    expect(cliModelDetails(info({ cli: { codex: { model_details: 'x' } } }), 'codex')).toBeNull();
    expect(cliModelDetails(info({ cli: { codex: { available: true } } }), 'codex')).toBeNull();
    expect(cliModelDetails(info({ cli: {} }), 'codex')).toBeNull();
    expect(cliModelDetails(undefined, 'codex')).toBeNull();
  });
});

describe('gitCapability', () => {
  it('reads an explicit true and nothing else', () => {
    // The opposite default from `cliAvailable`: a control that would 404 on
    // an older agent must not appear on a guess, so even the stringified form
    // is not enough here.
    const probe = info({ git: { changes: true, stage: true, discard: 'true', commit: false } });

    expect(gitCapability(probe, 'changes')).toBe(true);
    expect(gitCapability(probe, 'stage')).toBe(true);
    expect(gitCapability(probe, 'discard')).toBe(false);
    expect(gitCapability(probe, 'commit')).toBe(false);
  });

  const READ_ONLY: [string, unknown][] = [
    ['no probe at all', undefined],
    ['no capabilities', {}],
    ['a git block of the wrong shape', { git: 'yes' }],
    ['a git block without the key', { git: { changes: true } }],
    ['a null flag', { git: { stage: null } }],
  ];

  it.each(READ_ONLY)('answers false for %s, which keeps the viewer read-only', (_label, capabilities) => {
    const probe = capabilities === undefined ? undefined : info(capabilities);

    expect(gitCapability(probe, 'stage')).toBe(false);
  });

  it('does not mistake an inherited object key for a capability', () => {
    expect(gitCapability(info({ git: {} }), 'constructor')).toBe(false);
  });
});

describe('gitCapabilities', () => {
  it('collects the three mutating flags, each on its own', () => {
    expect(gitCapabilities(info({ git: { changes: true, stage: true, commit: true } }))).toEqual({
      stage: true,
      discard: false,
      commit: true,
      fetch: false,
      pull: false,
      push: false,
      branches: false,
      rebase: false,
      resolve: false,
      deleteRemote: false,
      conflicts: false,
    });
  });

  it('reads rebase and resolve each on its own', () => {
    expect(gitCapabilities(info({ git: { changes: true, stage: true, rebase: true } }))).toMatchObject({
      rebase: true,
      resolve: false,
    });
    expect(gitCapabilities(info({ git: { changes: true, stage: true, resolve: true } }))).toMatchObject({
      rebase: false,
      resolve: true,
    });
  });

  it('reads conflicts on its own, not implied by resolve or rebase', () => {
    expect(gitCapabilities(info({ git: { changes: true, conflicts: true } }))).toMatchObject({
      rebase: false,
      resolve: false,
      conflicts: true,
    });
    expect(
      gitCapabilities(info({ git: { changes: true, rebase: true, resolve: true } })).conflicts,
    ).toBe(false);
  });

  it('is all-off without a probe', () => {
    expect(gitCapabilities(undefined)).toEqual(NO_GIT_CAPABILITIES);
    expect(NO_GIT_CAPABILITIES).toEqual({
      stage: false,
      discard: false,
      commit: false,
      fetch: false,
      pull: false,
      push: false,
      branches: false,
      rebase: false,
      resolve: false,
      deleteRemote: false,
      conflicts: false,
    });
  });
});

describe('hasGitSync', () => {
  it('is true on any of the three job keys, each on its own', () => {
    // The menu entry and the Sync button show on any key; each sheet row
    // gates itself on its own key.
    expect(hasGitSync(gitCapabilities(info({ git: { changes: true, fetch: true } })))).toBe(true);
    expect(hasGitSync(gitCapabilities(info({ git: { pull: true } })))).toBe(true);
    expect(hasGitSync(gitCapabilities(info({ git: { push: true } })))).toBe(true);
  });

  it('is false for an agent that only stages and commits', () => {
    expect(hasGitSync(gitCapabilities(info({ git: { changes: true, stage: true, discard: true, commit: true } })))).toBe(false);
    expect(hasGitSync(NO_GIT_CAPABILITIES)).toBe(false);
  });
});
