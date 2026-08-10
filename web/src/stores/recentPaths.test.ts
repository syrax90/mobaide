import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../test/localStorageStub';

// Before the module under test, so its bare `localStorage` calls resolve to the
// stand-in rather than Node's own throwing implementation.
const storage = stubLocalStorage();

const {
  MAX_RECENT_CUSTOM_PATHS,
  clearRecentPaths,
  forgetRecentPath,
  noteRecentPath,
  readRecentPaths,
} = await import('./recentPaths');

const KEY = 'recent_custom_paths::claude';

/** Seed the way a hand-edited entry or an older build's blob arrives. */
function seedRaw(agentId: string, raw: string): void {
  storage.set(`recent_custom_paths::${agentId}`, raw);
}

function note(agentId: string, ...paths: string[]): void {
  for (const path of paths) noteRecentPath(agentId, path);
}

beforeEach(() => {
  storage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('noteRecentPath', () => {
  it('records a launched directory newest-first', () => {
    note('claude', '/one', '/two', '/three');

    expect(readRecentPaths('claude')).toEqual(['/three', '/two', '/one']);
  });

  it('moves a repeat to the front instead of listing it twice', () => {
    note('claude', '/one', '/two', '/three', '/one');

    expect(readRecentPaths('claude')).toEqual(['/one', '/three', '/two']);
  });

  it('drops the oldest directory past the cap', () => {
    const paths = Array.from({ length: MAX_RECENT_CUSTOM_PATHS + 2 }, (_, i) => `/p${String(i)}`);
    note('claude', ...paths);

    const recents = readRecentPaths('claude');
    expect(recents).toHaveLength(MAX_RECENT_CUSTOM_PATHS);
    expect(recents[0]).toBe(paths.at(-1));
    expect(recents).not.toContain('/p0');
  });

  it('never records a whitespace-only path', () => {
    note('claude', '/one', '   ', '');

    expect(readRecentPaths('claude')).toEqual(['/one']);
  });

  it('keeps each agent in its own bucket', () => {
    note('claude', '/one');
    note('codex', '/two');

    expect(readRecentPaths('claude')).toEqual(['/one']);
    expect(readRecentPaths('codex')).toEqual(['/two']);
  });

  it('trims the recorded path but normalises nothing else about it', () => {
    // Dedupe is exact string equality on both mobile clients: no trailing-slash
    // collapse, no `~` expansion. A separator-mangling encoding would show up
    // here first.
    note('claude', '  /srv/app  ', '/srv/app/', 'D:\\projects\\demo', '\\\\server\\share');

    expect(readRecentPaths('claude')).toEqual([
      '\\\\server\\share',
      'D:\\projects\\demo',
      '/srv/app/',
      '/srv/app',
    ]);
  });

  it('writes the bucket under the key Android and iOS use verbatim', () => {
    note('claude', '/one');

    expect([...storage.keys()]).toEqual([KEY]);
    expect(JSON.parse(storage.get(KEY) ?? '')).toEqual(['/one']);
  });
});

describe('readRecentPaths', () => {
  it('degrades to nothing for a value it cannot use', () => {
    seedRaw('claude', 'not json at all');
    expect(readRecentPaths('claude')).toEqual([]);

    seedRaw('claude', '{"paths":["/one"]}');
    expect(readRecentPaths('claude')).toEqual([]);

    seedRaw('claude', '"/one"');
    expect(readRecentPaths('claude')).toEqual([]);
  });

  it('voids a hand-edited array carrying anything but usable paths', () => {
    // Both mobile decoders are typed, so a mixed array fails outright rather
    // than yielding the readable members.
    seedRaw('claude', '["/one",42,"/two"]');
    expect(readRecentPaths('claude')).toEqual([]);

    seedRaw('claude', '["/one",null,"/two"]');
    expect(readRecentPaths('claude')).toEqual([]);

    seedRaw('claude', '["/one","   ","/two"]');
    expect(readRecentPaths('claude')).toEqual([]);
  });

  it('truncates an oversized blob to the cap', () => {
    seedRaw('claude', JSON.stringify(Array.from({ length: 12 }, (_, i) => `/p${String(i)}`)));

    expect(readRecentPaths('claude')).toHaveLength(MAX_RECENT_CUSTOM_PATHS);
  });
});

describe('forgetRecentPath', () => {
  it('leaves the list alone for a path that is only a prefix of a stored one', () => {
    note('claude', '/one', '/two', '/three');

    forgetRecentPath('claude', '/tw');

    expect(readRecentPaths('claude')).toEqual(['/three', '/two', '/one']);
  });

  it('drops the exact match, trimming like the add path, and keeps the survivors in order', () => {
    note('claude', '/one', '/two', '/three');

    forgetRecentPath('claude', '  /two  ');

    expect(readRecentPaths('claude')).toEqual(['/three', '/one']);
  });

  it('deletes an emptied bucket rather than parking an "[]" blob', () => {
    // The key embeds an agent id that may never come back, so the blob would be
    // permanent litter. iOS's `removeRecentCustomPath` deletes for the same
    // reason.
    note('claude', '/one');
    note('codex', '/two');

    forgetRecentPath('claude', '/one');

    expect(storage.has(KEY)).toBe(false);
    expect(readRecentPaths('codex')).toEqual(['/two']);
  });
});

describe('clearRecentPaths', () => {
  it('drops the deleted agent bucket and touches no other agent', () => {
    note('claude', '/one', '/two');
    note('codex', '/three');

    clearRecentPaths('claude');

    expect(storage.has(KEY)).toBe(false);
    expect(readRecentPaths('codex')).toEqual(['/three']);
  });
});

describe('an unresolved agent id', () => {
  it('reads nothing and writes nothing', () => {
    // The web equivalent of mobile's null `agentScopeKey`: an empty id must not
    // share one unscoped bucket across agents.
    seedRaw('', '["/seeded"]');

    noteRecentPath('', '/one');

    expect(readRecentPaths('')).toEqual([]);
    expect(storage.get('recent_custom_paths::')).toBe('["/seeded"]');
  });
});

describe('when storage refuses', () => {
  it('does not propagate a write a partitioned or full store rejects', () => {
    note('claude', '/one');
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });
    vi.spyOn(window.localStorage, 'removeItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });

    expect(() => {
      noteRecentPath('claude', '/two');
    }).not.toThrow();
    expect(() => {
      forgetRecentPath('claude', '/one');
    }).not.toThrow();
    expect(() => {
      clearRecentPaths('claude');
    }).not.toThrow();
  });

  it('reports no recents when even the read throws', () => {
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('access denied');
    });

    expect(readRecentPaths('claude')).toEqual([]);
  });
});
