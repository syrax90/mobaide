import { describe, expect, it } from 'vitest';
import {
  NO_GIT_CAPABILITIES,
  agentVersionSchema,
  cliAvailable,
  cliKnown,
  cliModelDetails,
  cliModels,
  cliModelsPending,
  fsCapability,
  gitCapabilities,
  gitCapability,
  hasGitSync,
  voiceCapability,
  voiceCleanupCapability,
  voiceCleanupKinds,
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
    ['an unlisted cli', { cli: { codex: { available: true } }, extra: 1 }],
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

describe('cliKnown', () => {
  it('is false only for an agent that lists its CLIs without this one', () => {
    // Such an agent predates the CLI and would open a plain shell for it.
    const older = info({ cli: { claude: { available: true }, codex: { available: false } } });

    expect(cliKnown(older, 'copilot')).toBe(false);
    expect(cliKnown(older, 'codex')).toBe(true);
  });

  it('counts a CLI the agent lists as not installed as known', () => {
    expect(cliKnown(info({ cli: { copilot: { available: false } } }), 'copilot')).toBe(true);
  });

  const UNKNOWN: [string, unknown][] = [
    ['no probe at all', undefined],
    ['no capabilities', {}],
    ['an empty cli map', { cli: {} }],
    ['a cli block that is not a map', { cli: ['claude'] }],
  ];

  it.each(UNKNOWN)('reads %s as known, so the card stays usable', (_label, capabilities) => {
    const probe = capabilities === undefined ? undefined : info(capabilities);

    expect(cliKnown(probe, 'copilot')).toBe(true);
  });

  it('does not mistake an inherited object key for a cli entry', () => {
    expect(cliKnown(info({ cli: { claude: { available: true } } }), 'constructor')).toBe(false);
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
    ['a git block without the key', { git: { changes: true } }],
    ['a null flag', { git: { stage: null } }],
  ];

  it.each(READ_ONLY)('answers false for %s, which keeps the viewer read-only', (_label, capabilities) => {
    const probe = capabilities === undefined ? undefined : info(capabilities);

    expect(gitCapability(probe, 'stage')).toBe(false);
  });
});

describe('fsCapability', () => {
  it('offers folder suggestions only on an explicit dirs: true', () => {
    // An agent predating `GET /api/fs/dirs` would 404 every keystroke.
    expect(fsCapability(info({ fs: { dirs: true } }), 'dirs')).toBe(true);
    expect(fsCapability(info({ fs: { dirs: 'true' } }), 'dirs')).toBe(false);
    expect(fsCapability(info({ fs: 'yes' }), 'dirs')).toBe(false);
    expect(fsCapability(info({ git: { changes: true } }), 'dirs')).toBe(false);
    expect(fsCapability(info({ fs: {} }), 'constructor')).toBe(false);
    expect(fsCapability(undefined, 'dirs')).toBe(false);
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

describe('voiceCapability', () => {
  const VOICE = { dictation: true, sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 };

  it('reads the dictation limits the agent advertises', () => {
    expect(voiceCapability(info({ voice: VOICE }))).toEqual({
      sampleRate: 16000,
      maxSeconds: 120,
      maxChunkBytes: 65536,
    });
  });

  const NO_VOICE: [string, unknown][] = [
    ['no probe at all', undefined],
    ['an agent too old for voice', {}],
    ['dictation as a string', { voice: { ...VOICE, dictation: 'true' } }],
    ['a zero limit', { voice: { ...VOICE, max_seconds: 0 } }],
    ['a stringified limit', { voice: { ...VOICE, max_chunk_bytes: '65536' } }],
  ];

  it.each(NO_VOICE)('answers null for %s, which hides the mic', (_label, capabilities) => {
    const probe = capabilities === undefined ? undefined : info(capabilities);

    expect(voiceCapability(probe)).toBeNull();
  });
});

describe('voiceCleanupCapability', () => {
  const VOICE = { dictation: true, sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 };

  it('offers transcript cleanup only on an explicit cleanup: true', () => {
    // An agent predating `POST /api/voice/cleanup` would 404 every dictation.
    expect(voiceCleanupCapability(info({ voice: { ...VOICE, cleanup: true } }))).toBe(true);
  });

  const NO_CLEANUP: [string, unknown][] = [
    ['no probe at all', undefined],
    ['cleanup off', { voice: { ...VOICE, cleanup: false } }],
    ['cleanup as a string', { voice: { ...VOICE, cleanup: 'true' } }],
  ];

  it.each(NO_CLEANUP)('answers false for %s, which hides the cleanup option', (_label, capabilities) => {
    const probe = capabilities === undefined ? undefined : info(capabilities);

    expect(voiceCleanupCapability(probe)).toBe(false);
  });
});

describe('voiceCleanupKinds', () => {
  const VOICE = { dictation: true, sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 };

  it('lists the session kinds the agent advertises', () => {
    const probe = info({ voice: { ...VOICE, cleanup: true, cleanup_kinds: ['claude', 'codex', 'antigravity'] } });

    expect(voiceCleanupKinds(probe)).toEqual(['claude', 'codex', 'antigravity']);
  });

  it('keeps a kind this build does not know, so a newer agent can offer it', () => {
    const probe = info({ voice: { ...VOICE, cleanup: true, cleanup_kinds: ['codex', 'opencode'] } });

    expect(voiceCleanupKinds(probe)).toEqual(['codex', 'opencode']);
  });

  const CLAUDE_ONLY: [string, unknown][] = [
    ['no list (an agent before Codex and Antigravity cleanup)', undefined],
    ['a list of the wrong shape', 'claude,codex'],
  ];

  it.each(CLAUDE_ONLY)('reads %s as Claude Code only', (_label, kinds) => {
    const voice = kinds === undefined ? { ...VOICE, cleanup: true } : { ...VOICE, cleanup: true, cleanup_kinds: kinds };

    expect(voiceCleanupKinds(info({ voice }))).toEqual(['claude']);
  });

  it('lists nothing without cleanup: true, whatever the list says', () => {
    expect(voiceCleanupKinds(undefined)).toEqual([]);
    expect(voiceCleanupKinds(info({ voice: { ...VOICE, cleanup: false, cleanup_kinds: ['codex'] } }))).toEqual([]);
  });
});
