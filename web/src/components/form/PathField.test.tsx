import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FsDirsResponse } from '../../api/fsRepository';

// The collaborator is the repository, not the wire: each listing request is
// recorded, and answered from `api.listings` by `dir|q` (a missing entry is a
// request the test did not expect, so it fails loudly).
const api = vi.hoisted(() => ({
  requests: [] as { path: string; q: string | null }[],
  listings: new Map<string, () => Promise<FsDirsResponse>>(),
  versionProbes: 0,
}));

vi.mock('../../api/fsRepository', () => ({
  listDirs: (_agentId: string, path: string, q: string | null) => {
    api.requests.push({ path, q });
    const answer = api.listings.get(`${path}|${q ?? ''}`);
    return answer === undefined ? Promise.reject(new Error(`unexpected listing ${path}`)) : answer();
  },
}));

vi.mock('../../api/agentCapabilities', () => ({
  fetchAgentCapabilities: () => {
    api.versionProbes += 1;
    return Promise.reject(new Error('the capability comes from the seeded cache'));
  },
}));

const { ApiError } = await import('../../api/errors');
const { queryKeys } = await import('../../lib/queryKeys');
const { agentVersionSchema } = await import('../../protocol/dto/agentVersion');
const { SUGGESTION_DEBOUNCE_MS } = await import('../../lib/pathCompletion');
const { PathField } = await import('./PathField');

let client: QueryClient;
/** Every value the field asked its owner to store, in order. */
const changes: string[] = [];
// Listings are cached per agent at module scope, so each test browses its own.
let agentSeq = 0;
let agentId = '';

function answer(path: string, q: string | null, dirs: string[], truncated = false): void {
  api.listings.set(`${path}|${q ?? ''}`, () =>
    Promise.resolve({ path: path.replace(/^~\//, '/home/me/'), dirs, truncated }),
  );
}

function fail(path: string, status: number): void {
  api.listings.set(`${path}|`, () =>
    Promise.reject(
      new ApiError({ status, message: 'no', code: null, retryAfterSeconds: null, detail: 'no' }),
    ),
  );
}

function Harness({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial);
  return (
    <PathField
      label="Path"
      agentId={agentId}
      value={value}
      onChange={(next) => {
        changes.push(next);
        setValue(next);
      }}
    />
  );
}

function mount(initial = '', supported = true, windowsPaths = false): HTMLInputElement {
  client.setQueryData(queryKeys.agentFsDirs(agentId), { dirs: supported, windowsPaths });
  render(
    <QueryClientProvider client={client}>
      <Harness initial={initial} />
    </QueryClientProvider>,
  );
  const input = screen.getByRole<HTMLInputElement>('combobox', { name: 'Path' });
  act(() => {
    input.focus();
  });
  return input;
}

async function settle(ms = SUGGESTION_DEBOUNCE_MS): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function type(input: HTMLInputElement, text: string): Promise<void> {
  fireEvent.change(input, { target: { value: text } });
  await settle();
}

const options = (): string[] =>
  screen.queryAllByRole('option').map((node) => node.textContent ?? '');

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.requests.length = 0;
  api.listings.clear();
  api.versionProbes = 0;
  changes.length = 0;
  agentSeq += 1;
  agentId = `agent-path-${agentSeq}`;
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.useRealTimers();
});

describe('PathField listing', () => {
  it('lists a directory once, after the debounce, and filters it locally while typing', async () => {
    answer('/', null, ['.hidden', 'Users', 'usr', 'opt']);
    const input = mount();

    fireEvent.change(input, { target: { value: '/' } });
    await settle(SUGGESTION_DEBOUNCE_MS - 1);
    expect(api.requests).toEqual([]);

    await settle(1);
    expect(api.requests).toEqual([{ path: '/', q: null }]);
    expect(options()).toEqual(['Users/', 'usr/', 'opt/']);
    expect(input).toHaveAttribute('aria-expanded', 'true');

    await type(input, '/us');
    await type(input, '/Use');

    expect(api.requests).toHaveLength(1);
    expect(options()).toEqual(['Users/']);
  });

  it('asks again with the typed fragment as q only when the listing was truncated', async () => {
    answer('/big/', null, ['aaa'], true);
    answer('/big/', 'pro', ['project']);
    const input = mount();

    await type(input, '/big/');
    await type(input, '/big/pro');

    expect(api.requests).toEqual([
      { path: '/big/', q: null },
      { path: '/big/', q: 'pro' },
    ]);
    expect(options()).toEqual(['project/']);
  });

  it('remembers a missing directory, so typing under it stays local', async () => {
    fail('/nope/', 404);
    const input = mount();

    await type(input, '/nope/');
    await type(input, '/nope/abc');

    expect(api.requests).toHaveLength(1);
    expect(options()).toEqual([]);
    expect(input).toHaveAttribute('aria-expanded', 'false');
  });

  it('does not remember a busy agent, so coming back to the directory asks again', async () => {
    fail('/srv/', 503);
    answer('/', null, ['srv']);
    const input = mount();

    await type(input, '/srv/');
    await type(input, '/srv/x');
    expect(api.requests).toEqual([{ path: '/srv/', q: null }]);

    await type(input, '/');
    await type(input, '/srv/');

    expect(api.requests).toEqual([
      { path: '/srv/', q: null },
      { path: '/', q: null },
      { path: '/srv/', q: null },
    ]);
  });
});

describe('PathField gating', () => {
  it('asks nothing of an agent without capabilities.fs.dirs', async () => {
    const input = mount('', false);

    await type(input, '/');

    expect(api.requests).toEqual([]);
    expect(options()).toEqual([]);
  });

  it('reuses the wizard probe instead of asking /api/version again', async () => {
    const seed = (fs: unknown) =>
      agentVersionSchema.parse({
        name: 'mobaide-agent',
        server_version: '1.4.0',
        protocol_versions: [7],
        min_client_version: 1,
        capabilities: fs === undefined ? {} : { fs },
      });
    client.setQueryData(queryKeys.agentCapabilities(agentId), seed({ dirs: true }));
    answer('/', null, ['srv']);
    render(
      <QueryClientProvider client={client}>
        <Harness initial="" />
      </QueryClientProvider>,
    );
    const input = screen.getByRole<HTMLInputElement>('combobox', { name: 'Path' });
    act(() => {
      input.focus();
    });

    await type(input, '/');

    expect(api.versionProbes).toBe(0);
    expect(options()).toEqual(['srv/']);
  });

  it('asks nothing for a relative path and hints how to start browsing', async () => {
    const input = mount();

    expect(screen.getByText('Type / or ~ to browse folders on the agent.')).toBeInTheDocument();

    await type(input, 'code/');

    expect(api.requests).toEqual([]);
  });

  it('hides the list once the field loses focus', async () => {
    answer('/', null, ['srv']);
    const input = mount();
    await type(input, '/');
    expect(options()).toEqual(['srv/']);

    act(() => {
      input.blur();
    });

    expect(options()).toEqual([]);
    expect(input).toHaveAttribute('aria-expanded', 'false');
  });
});

describe('PathField keyboard', () => {
  async function openAt(dir: string, dirs: string[]): Promise<HTMLInputElement> {
    answer(dir, null, dirs);
    const input = mount();
    await type(input, dir);
    return input;
  }

  it('moves the highlight with the arrows, wrapping, and points aria-activedescendant at it', async () => {
    const input = await openAt('/', ['alpha', 'beta']);

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    const [first, second] = screen.getAllByRole('option');
    expect(first).toHaveAttribute('aria-selected', 'true');
    expect(input).toHaveAttribute('aria-activedescendant', first?.id);

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(first).toHaveAttribute('aria-selected', 'true');

    fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(second).toHaveAttribute('aria-selected', 'true');
  });

  it('writes the picked folder with a trailing separator on Enter and lists it next', async () => {
    const input = await openAt('/', ['alpha', 'beta']);
    answer('/beta/', null, ['inner']);

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    const prevented = !fireEvent.keyDown(input, { key: 'Enter' });
    await settle();

    expect(prevented).toBe(true);
    expect(changes.at(-1)).toBe('/beta/');
    expect(input).toHaveValue('/beta/');
    expect(options()).toEqual(['inner/']);
  });

  it('writes the agent-expanded path when the typed one started with ~', async () => {
    await openAt('~/', ['code']);

    fireEvent.click(screen.getByRole('option', { name: 'code/' }));

    expect(changes.at(-1)).toBe('/home/me/code/');
  });

  it('leaves Tab alone until a folder is highlighted', async () => {
    const input = await openAt('/', ['alpha']);

    expect(fireEvent.keyDown(input, { key: 'Tab' })).toBe(true);
    expect(changes).toEqual(['/']);

    fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(fireEvent.keyDown(input, { key: 'Tab' })).toBe(false);
    expect(changes.at(-1)).toBe('/alpha/');
  });

  it('closes on Escape without letting a dialog see it, until the text changes', async () => {
    const input = await openAt('/', ['alpha', 'axe']);

    expect(fireEvent.keyDown(input, { key: 'Escape' })).toBe(false);
    expect(options()).toEqual([]);
    expect(input).toHaveAttribute('aria-expanded', 'false');

    await type(input, '/a');
    expect(options()).toEqual(['alpha/', 'axe/']);
  });

  it('closes on Enter with nothing highlighted, still letting the form see it', async () => {
    const input = await openAt('/', ['alpha', 'axe']);

    expect(fireEvent.keyDown(input, { key: 'Enter' })).toBe(true);
    expect(options()).toEqual([]);
    expect(changes.at(-1)).toBe('/');

    await type(input, '/a');
    expect(options()).toEqual(['alpha/', 'axe/']);
  });

  it('lets a closed list Escape through to the dialog', () => {
    const input = mount();

    expect(fireEvent.keyDown(input, { key: 'Escape' })).toBe(true);
  });
});
