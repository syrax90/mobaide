import { describe, expect, it } from 'vitest';
import {
  DirListingCache,
  LISTING_TTL_MS,
  acceptSuggestion,
  nextListingRequest,
  rankDirs,
  splitPathQuery,
  trimTrailingSeparator,
  type DirListing,
} from './pathCompletion';

const listing = (path: string, dirs: string[], truncated = false): DirListing => ({
  path,
  dirs,
  truncated,
});

/** A cache on a hand-driven clock, so TTL cases never sleep. */
function clockedCache(): { cache: DirListingCache; advance: (ms: number) => void } {
  let now = 1_000_000;
  return {
    cache: new DirListingCache(() => now),
    advance: (ms) => {
      now += ms;
    },
  };
}

describe('splitPathQuery', () => {
  it.each([
    ['/', '/', ''],
    ['/Us', '/', 'Us'],
    ['/Users/pa', '/Users/', 'pa'],
    ['/Users/pavel/', '/Users/pavel/', ''],
    ['~/co', '~/', 'co'],
    ['C:\\Users\\Pa', 'C:\\Users\\', 'Pa'],
    ['c:/work/x', 'c:/work/', 'x'],
  ])('lists the directory up to the last separator of %s', (text, dir, fragment) => {
    expect(splitPathQuery(text)).toEqual({ dir, fragment });
  });

  it('treats a bare ~ as the home directory', () => {
    expect(splitPathQuery('~')).toEqual({ dir: '~/', fragment: '' });
  });

  it.each([
    ['empty', ''],
    ['a relative path', 'code/proj'],
    ['a bare drive letter', 'C:'],
    ['a ~user with no separator yet', '~pavel'],
  ])('offers nothing for %s', (_label, text) => {
    expect(splitPathQuery(text)).toBeNull();
  });
});

describe('rankDirs', () => {
  it('puts prefix matches before mere substring matches, each in the agent order', () => {
    expect(rankDirs(['MyCode', 'code-b', 'barcode', 'Code-a', 'docs'], 'code')).toEqual([
      'code-b',
      'Code-a',
      'MyCode',
      'barcode',
    ]);
  });

  it('hides dot directories until the fragment itself starts with a dot', () => {
    const dirs = ['.config', '.git', 'config'];

    expect(rankDirs(dirs, '')).toEqual(['config']);
    expect(rankDirs(dirs, 'conf')).toEqual(['config']);
    expect(rankDirs(dirs, '.')).toEqual(['.config', '.git']);
    expect(rankDirs(dirs, '.CON')).toEqual(['.config']);
  });
});

describe('acceptSuggestion', () => {
  it.each([
    ['/Users', 'pavel', '/Users/pavel/'],
    ['/Users/', 'pavel', '/Users/pavel/'],
    ['/', 'Users', '/Users/'],
    // The agent echoes the `~`-expanded directory, so a pick writes it absolute.
    ['/home/me', 'code', '/home/me/code/'],
    ['C:\\Users', 'me', 'C:\\Users\\me\\'],
    ['C:\\', 'Users', 'C:\\Users\\'],
    ['C:/Users', 'me', 'C:/Users/me/'],
  ])('appends to %s the picked name and a separator in its own style', (listed, name, text) => {
    expect(acceptSuggestion(listed, name)).toBe(text);
  });
});

describe('trimTrailingSeparator', () => {
  it.each([
    ['/a/b/', '/a/b'],
    ['C:\\work\\', 'C:\\work'],
    ['/a//', '/a/'],
    ['/a/b', '/a/b'],
  ])('drops one trailing separator from %s', (path, trimmed) => {
    expect(trimTrailingSeparator(path)).toBe(trimmed);
  });

  it.each(['/', 'C:\\', 'C:/', ''])('keeps the root %j as it is', (root) => {
    expect(trimTrailingSeparator(root)).toBe(root);
  });
});

describe('DirListingCache', () => {
  it('answers every fragment of a directory from one complete unfiltered listing', () => {
    const { cache } = clockedCache();
    cache.store('/srv/', null, listing('/srv', ['a', 'b']));

    expect(cache.lookup('/srv/', '')).toEqual({ listing: listing('/srv', ['a', 'b']), complete: true });
    expect(cache.lookup('/srv/', 'zz')?.complete).toBe(true);
  });

  it('marks a truncated listing as shown but not complete', () => {
    const { cache } = clockedCache();
    cache.store('/big/', null, listing('/big', ['a'], true));

    expect(cache.lookup('/big/', 'a')).toEqual({ listing: listing('/big', ['a'], true), complete: false });
  });

  it('lets a filtered listing answer only the fragments that contain its q', () => {
    // Every name containing `xaby` contains `ab`, so the `ab` listing misses
    // nothing; it cannot answer `a`, which names without `ab` would also match.
    const { cache } = clockedCache();
    cache.store('/big/', 'AB', listing('/big', ['abc', 'xab']));

    expect(cache.lookup('/big/', 'xaby')?.complete).toBe(true);
    expect(cache.lookup('/big/', 'ab')?.complete).toBe(true);
    expect(cache.lookup('/big/', 'a')).toBeUndefined();
  });

  it('never answers a directory from a listing of its child', () => {
    const { cache } = clockedCache();
    cache.store('/a/b/', null, listing('/a/b', ['c']));

    expect(cache.lookup('/a/', '')).toBeUndefined();
    expect(cache.hasUnfiltered('/a/')).toBe(false);
  });

  it('keeps a listing for the TTL and drops it after', () => {
    const { cache, advance } = clockedCache();
    cache.store('/srv/', null, listing('/srv', ['a']));

    advance(LISTING_TTL_MS);
    expect(cache.lookup('/srv/', '')?.complete).toBe(true);
    expect(cache.hasUnfiltered('/srv/')).toBe(true);

    advance(1);
    expect(cache.hasUnfiltered('/srv/')).toBe(false);
    expect(cache.lookup('/srv/', '')).toBeUndefined();
  });

  it('evicts the least recently used listing past 32, counting a lookup as a use', () => {
    const { cache } = clockedCache();
    cache.store('/kept/', null, listing('/kept', ['k']));
    for (let i = 0; i < 31; i += 1) cache.store(`/d${i}/`, null, listing(`/d${i}`, []));

    expect(cache.lookup('/kept/', '')).toBeDefined();
    cache.store('/new/', null, listing('/new', []));

    expect(cache.hasUnfiltered('/kept/')).toBe(true);
    expect(cache.hasUnfiltered('/d0/')).toBe(false);
    expect(cache.hasUnfiltered('/d1/')).toBe(true);
  });
});

describe('nextListingRequest', () => {
  it('lists a directory nobody has listed yet, unfiltered', () => {
    const { cache } = clockedCache();

    expect(nextListingRequest(cache, { dir: '/srv/', fragment: 'pro' })).toEqual({
      dir: '/srv/',
      q: null,
    });
  });

  it('asks nothing more while typing inside a completely listed directory', () => {
    const { cache } = clockedCache();
    cache.store('/srv/', null, listing('/srv', ['proj']));

    expect(nextListingRequest(cache, { dir: '/srv/', fragment: '' })).toBeNull();
    expect(nextListingRequest(cache, { dir: '/srv/', fragment: 'pr' })).toBeNull();
    expect(nextListingRequest(cache, { dir: '/srv/', fragment: 'proj' })).toBeNull();
  });

  it('re-asks a truncated directory with the fragment as q, and only with one', () => {
    const { cache } = clockedCache();
    cache.store('/big/', null, listing('/big', ['a'], true));

    expect(nextListingRequest(cache, { dir: '/big/', fragment: '' })).toBeNull();
    expect(nextListingRequest(cache, { dir: '/big/', fragment: 'ab' })).toEqual({
      dir: '/big/',
      q: 'ab',
    });

    cache.store('/big/', 'ab', listing('/big', ['abc']));
    expect(nextListingRequest(cache, { dir: '/big/', fragment: 'abc' })).toBeNull();
  });
});
