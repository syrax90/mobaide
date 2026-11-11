import { afterEach, describe, expect, it, vi } from 'vitest';

// Hand-written stand-in for the typed client, as in `authRepository.test.ts`:
// `relayClient` is built with the SPA's relative base URL, which the undici
// Request under vitest refuses to parse. The repository's own mapping is what
// is under test, so the queue below plays the part of the wire.
const relay = vi.hoisted(() => ({
  results: [] as { data?: unknown; error?: unknown; response: Response }[],
  paths: [] as string[],
}));

vi.mock('./clients', () => ({
  relayClient: {
    GET: (path: string) => {
      relay.paths.push(path);
      const next = relay.results.shift();
      if (next === undefined) return Promise.reject(new Error(`unexpected GET ${path}`));
      return Promise.resolve(next);
    },
  },
}));

const { groupTemplates, listTemplates } = await import('./templateRepository');
const { ApiError } = await import('./errors');

type PromptTemplate = Awaited<ReturnType<typeof listTemplates>>[number];

afterEach(() => {
  relay.results.length = 0;
  relay.paths.length = 0;
});

function template(
  templateId: string,
  over: Partial<Omit<PromptTemplate, 'templateId'>> = {},
): PromptTemplate {
  return {
    templateId,
    name: templateId,
    body: `body of ${templateId}`,
    isFavorite: false,
    lastUsedAt: null,
    ...over,
  };
}

/** `2026-08-04T10:00:00Z` style stamps, ordered by the trailing hour. */
const usedAt = (hour: number): string =>
  `2026-08-04T${String(hour).padStart(2, '0')}:00:00Z`;

const ids = (templates: PromptTemplate[]): string[] =>
  templates.map((entry) => entry.templateId);

describe('groupTemplates', () => {
  it('orders recents by last use, newest first', () => {
    const grouped = groupTemplates([
      template('older', { lastUsedAt: usedAt(9) }),
      template('newest', { lastUsedAt: usedAt(18) }),
      template('middle', { lastUsedAt: usedAt(12) }),
      template('never'),
    ]);

    expect(ids(grouped.recents)).toEqual(['newest', 'middle', 'older']);
    expect(ids(grouped.others)).toEqual(['never']);
  });

  it('keeps at most five recents and leaves the rest to the other groups', () => {
    const used = Array.from({ length: 7 }, (_, index) =>
      template(`t${String(index)}`, { lastUsedAt: usedAt(index) }),
    );

    const grouped = groupTemplates(used);

    expect(ids(grouped.recents)).toEqual(['t6', 't5', 't4', 't3', 't2']);
    expect(ids(grouped.others)).toEqual(['t0', 't1']);
  });

  it('shows a favourite that is also a recent only once', () => {
    const grouped = groupTemplates([
      template('pinned', { isFavorite: true, lastUsedAt: usedAt(10) }),
      template('starred', { isFavorite: true }),
    ]);

    expect(ids(grouped.recents)).toEqual(['pinned']);
    expect(ids(grouped.favorites)).toEqual(['starred']);
    expect(grouped.others).toEqual([]);
  });

  it('sorts favourites and others by name, ignoring case', () => {
    const grouped = groupTemplates([
      template('c', { name: 'cherry', isFavorite: true }),
      template('a', { name: 'Apple', isFavorite: true }),
      template('z', { name: 'zebra' }),
      template('b', { name: 'Banana' }),
    ]);

    expect(ids(grouped.favorites)).toEqual(['a', 'c']);
    expect(ids(grouped.others)).toEqual(['b', 'z']);
  });

  it('filters on the name or the body', () => {
    const templates = [
      template('byName', { name: 'Run the linter' }),
      template('byBody', { name: 'Unrelated', body: 'npm run LINT' }),
      template('neither', { name: 'Deploy', body: 'ship it' }),
    ];

    expect(ids(groupTemplates(templates, 'lint').others)).toEqual(['byName', 'byBody']);
  });

  it('filters before grouping, so a filtered-out recent frees a slot', () => {
    const templates = [
      template('keep', { name: 'keep me', lastUsedAt: usedAt(9) }),
      template('drop', { name: 'something else', lastUsedAt: usedAt(18) }),
    ];

    const grouped = groupTemplates(templates, 'keep');

    expect(ids(grouped.recents)).toEqual(['keep']);
    expect(grouped.others).toEqual([]);
  });
});

describe('listTemplates', () => {

  it('raises the relay error rather than handing the picker an empty list', async () => {
    // An empty list and a failed fetch look identical to the picker otherwise,
    // and a silently empty picker reads as "you have no templates".
    relay.results.push({
      error: { detail: 'Not authenticated' },
      response: new Response(null, { status: 401 }),
    });

    await expect(listTemplates()).rejects.toBeInstanceOf(ApiError);
  });
});
