/**
 * The four last-used-launch behaviours that exist only in the container.
 *
 * `applyLaunchPrefs` / `launchPrefsFromState` and the store's read-side
 * sanitising are covered by their own suites; what cannot be reached from
 * either is the wiring — the reducer's lazy seed, the pin taken before the
 * round trip, the per-write guard around a refused `setItem`, and the
 * one-shot project pre-fill effect.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useEffect } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { stubLocalStorage } from '../../../test/localStorageStub';
import type { ProjectInfo } from '../../../api/projectRepository';
import type { CreateSessionRequest } from '../../../api/sessionRepository';
import type { SessionResponse } from '../../../protocol/dto/session';
import type { LaunchPrefs } from '../../../stores/launchPrefsStore';

// No agent token means no URL, so the wizard's agent channel never constructs a
// socket — the same seam the other screen suites use.
vi.mock('../../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const api = vi.hoisted(
  (): {
    projects: ProjectInfo[];
    requests: CreateSessionRequest[];
    create: (request: CreateSessionRequest) => Promise<SessionResponse>;
  } => ({
    projects: [],
    requests: [],
    create: () => Promise.reject(new Error('createSession is not stubbed')),
  }),
);

vi.mock('../../../api/projectRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/projectRepository')>();
  return { ...actual, listProjects: () => Promise.resolve(api.projects) };
});

vi.mock('../../../api/sessionRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/sessionRepository')>();
  return {
    ...actual,
    createSession: (_agentId: string, request: CreateSessionRequest) => {
      api.requests.push(request);
      return api.create(request);
    },
  };
});

// Parsed through the real schema so the mode cards read it exactly as they read
// a live probe; Claude reported present keeps the config step reachable.
vi.mock('../../../api/agentCapabilities', async () => {
  const { agentVersionSchema } = await import('../../../protocol/dto/agentVersion');
  const info = agentVersionSchema.parse({
    name: 'agent-1',
    server_version: '1.0.0',
    protocol_versions: [8],
    min_client_version: 1,
    capabilities: { cli: { claude: { available: true } } },
  });
  return { fetchAgentCapabilities: () => Promise.resolve(info) };
});

vi.mock('../../../api/customAgentTypeRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/customAgentTypeRepository')>();
  return { ...actual, listCustomAgentTypes: () => Promise.resolve([]) };
});

// Before any persisted store is imported.
stubLocalStorage();

const { projectInfoSchema } = await import('../../../protocol/dto/projects');
const { sessionResponseSchema } = await import('../../../protocol/dto/session');
const { queryKeys } = await import('../../../lib/queryKeys');
const { useLastSessionStore } = await import('../../../stores/lastSessionStore');
const { useLaunchPrefsStore } = await import('../../../stores/launchPrefsStore');
const { stopAllRealtime } = await import('../../../realtime');
const { NewSession } = await import('./NewSession');

const STORAGE_KEY = 'mobaide-launch-prefs';

const NO_PREFS: LaunchPrefs = {
  claude: null,
  autoScan: null,
  codex: null,
  antigravity: null,
  lastProjectName: null,
};

/** What a Claude launch off an untouched wizard files. */
const CLAUDE_AT_DEFAULTS = {
  model: null,
  effort: 'high',
  fastMode: false,
  ultracode: false,
  appendSystemPrompt: '',
  skipPermissions: false,
  noMemory: false,
  planMode: false,
  debug: false,
};

const project = (name: string): ProjectInfo =>
  projectInfoSchema.parse({ name, path: `/srv/${name}` });

const gitProject = (name: string): ProjectInfo =>
  projectInfoSchema.parse({ name, path: `/srv/${name}`, is_git: true });

const session = (sessionId: string): SessionResponse =>
  sessionResponseSchema.parse({
    session_id: sessionId,
    state: 'running',
    created_at: '2026-08-12T10:00:00Z',
  });

async function seedPrefs(state: Record<string, unknown>) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ state, version: 0 }));
  await useLaunchPrefsStore.persist.rehydrate();
}

function persistedPrefs(): Partial<LaunchPrefs> {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === null) return {};
  return (JSON.parse(raw) as { state: Partial<LaunchPrefs> }).state;
}

interface Deferred {
  promise: Promise<SessionResponse>;
  settle: (value: SessionResponse) => void;
}

/** A create the test holds open, so the wizard can be driven mid-flight. */
function deferredSession(): Deferred {
  let settle!: (value: SessionResponse) => void;
  const promise = new Promise<SessionResponse>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

let client: QueryClient;
/** One entry per location the router actually navigated to. */
const visited: string[] = [];

function Probe() {
  const location = useLocation();
  useEffect(() => {
    visited.push(location.search);
  }, [location]);
  return <span data-testid="location">{`${location.pathname}${location.search}`}</span>;
}

const currentPath = (): string | null => screen.getByTestId('location').textContent;

async function renderWizard(search = '') {
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/agents/agent-1/new-session${search}`]}>
        <Probe />
        <Routes>
          <Route path="/agents/:agentId/new-session" element={<NewSession />} />
          {/* A destination for the create's navigate, so no location goes unmatched. */}
          <Route path="/agents/:agentId/sessions/:sessionId" element={<p>terminal</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // Every step is gated on the project list, and so is the pre-fill effect.
  await waitFor(() => {
    expect(screen.queryByText('Loading projects…')).not.toBeInTheDocument();
  });
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.projects = [project('alpha'), project('beta')];
  api.requests.length = 0;
  api.create = () => Promise.resolve(session('s-new'));
  visited.length = 0;
  // setState writes back through the persist middleware, so reset the
  // in-memory singleton first and clear storage after.
  useLaunchPrefsStore.setState(NO_PREFS);
  useLastSessionStore.setState({ lastUsedSessionIds: {} });
  window.localStorage.clear();
});

afterEach(() => {
  // First: the refused-write test leaves `setItem` throwing, which every reset
  // below would otherwise trip over.
  vi.restoreAllMocks();
  cleanup();
  stopAllRealtime();
  client.clear();
  useLaunchPrefsStore.setState(NO_PREFS);
  useLastSessionStore.setState({ lastUsedSessionIds: {} });
  window.localStorage.clear();
});

describe('NewSession launch prefs', () => {
  it('opens the Options step on the stored Claude options', async () => {
    await seedPrefs({
      claude: {
        ...CLAUDE_AT_DEFAULTS,
        model: 'sonnet',
        effort: 'max',
        skipPermissions: true,
        appendSystemPrompt: 'be terse',
      },
    });

    await renderWizard('?step=config&mode=claude&project=alpha');

    expect(screen.getByRole('heading', { name: 'Claude code session' })).toBeInTheDocument();
    expect(screen.getByLabelText('Model')).toHaveValue('sonnet');
    expect(screen.getByLabelText('Effort')).toHaveValue('max');
    expect(screen.getByLabelText('Skip permissions')).toBeChecked();
    expect(screen.getByLabelText('Append system prompt (optional)')).toHaveValue('be terse');
  });

  it('completes a launch whose preference writes storage refuses', async () => {
    await renderWizard('?step=config&mode=claude&project=alpha');
    fireEvent.click(screen.getByLabelText('Skip permissions'));
    // A full quota, private mode, a partitioned context: `persist` calls
    // `setItem` on every `set` with no guard of its own, and an escaping throw
    // is routed to the mutation's `onError` — reporting a session the agent has
    // already started as a failed launch.
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Create session' }));

    await waitFor(() => {
      expect(currentPath()).toBe('/agents/agent-1/sessions/s-new');
    });
    // The seed the terminal screen needs before the reconciling snapshot lands.
    expect(client.getQueryData(queryKeys.sessions('agent-1'))).toEqual([session('s-new')]);
    // Nothing durable survived, which is the whole cost of catching.
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    // One guard per write, not one around both: the throw on the options must
    // not swallow the project name behind it. `set` lands the in-memory half
    // before `persist` throws, so both halves are observable here.
    expect(useLaunchPrefsStore.getState().claude).toEqual({
      ...CLAUDE_AT_DEFAULTS,
      skipPermissions: true,
    });
    expect(useLaunchPrefsStore.getState().lastProjectName).toBe('alpha');
  });

  it('files the options the launch carried, not one toggled while it was in flight', async () => {
    const pending = deferredSession();
    api.create = () => pending.promise;

    await renderWizard('?step=config&mode=claude&project=alpha');
    fireEvent.click(screen.getByRole('button', { name: 'Create session' }));
    await screen.findByRole('button', { name: 'Creating…' });

    // Only Back, Create and Cancel are gated while submitting, so this is a
    // real user act — and the request already on the wire says nothing of it.
    fireEvent.click(screen.getByLabelText('Skip permissions'));
    expect(screen.getByLabelText('Skip permissions')).toBeChecked();

    await act(async () => {
      pending.settle(session('s-new'));
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(currentPath()).toBe('/agents/agent-1/sessions/s-new');
    });
    expect(api.requests[0]?.claude_options?.skip_permissions).toBe(false);
    expect(persistedPrefs().claude).toEqual(CLAUDE_AT_DEFAULTS);
  });

  it('pre-selects the last launched project with a single replacing write', async () => {
    await seedPrefs({ lastProjectName: 'beta' });

    await renderWizard();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /beta/ })).toHaveAttribute(
        'aria-pressed',
        'true',
      );
    });
    expect(currentPath()).toBe('/agents/agent-1/new-session?step=project&project=beta');
    // The latch is what keeps the clamp effect and this one from trading URLs.
    expect(visited).toEqual(['', '?step=project&project=beta']);
  });

  it('leaves the URL alone when the stored project is no longer on the agent', async () => {
    await seedPrefs({ lastProjectName: 'ghost' });

    await renderWizard();

    expect(screen.getByRole('heading', { name: 'Where should it run?' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /beta/ })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    expect(currentPath()).toBe('/agents/agent-1/new-session');
    expect(visited).toEqual(['']);
  });
});

describe('NewSession worktree options', () => {
  beforeEach(() => {
    // `is_git` defaults to **false** in `projectInfoSchema`, so the default
    // fixture renders no worktree group at all.
    api.projects = [gitProject('alpha')];
  });

  it('shows the branch name only while a worktree is requested', async () => {
    await renderWizard('?step=config&mode=claude&project=alpha');

    const worktree = screen.getByLabelText('Create worktree');
    expect(screen.queryByLabelText('Branch name (optional)')).not.toBeInTheDocument();

    fireEvent.click(worktree);
    expect(screen.getByLabelText('Branch name (optional)')).toBeInTheDocument();

    fireEvent.click(worktree);
    expect(screen.queryByLabelText('Branch name (optional)')).not.toBeInTheDocument();
  });

  it('gives the field back the branch the user already typed', async () => {
    await renderWizard('?step=config&mode=claude&project=alpha');

    const worktree = screen.getByLabelText('Create worktree');
    fireEvent.click(worktree);
    fireEvent.change(screen.getByLabelText('Branch name (optional)'), {
      target: { value: 'feature/my-change' },
    });

    // Unticking hides the field but must not discard the draft — the reducer
    // keeps it, exactly as both mobile clients do.
    fireEvent.click(worktree);
    fireEvent.click(worktree);

    expect(screen.getByLabelText('Branch name (optional)')).toHaveValue('feature/my-change');
  });
});
