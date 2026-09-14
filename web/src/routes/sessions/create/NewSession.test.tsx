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
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { useEffect } from 'react';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router';
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
    /** How many times the project list was fetched. */
    projectLists: number;
    projectWrites: { name: string; path: string }[];
    requests: CreateSessionRequest[];
    create: (request: CreateSessionRequest) => Promise<SessionResponse>;
  } => ({
    projects: [],
    projectLists: 0,
    projectWrites: [],
    requests: [],
    create: () => Promise.reject(new Error('createSession is not stubbed')),
  }),
);

vi.mock('../../../api/projectRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/projectRepository')>();
  const { projectInfoSchema } = await import('../../../protocol/dto/projects');
  return {
    ...actual,
    listProjects: () => {
      api.projectLists += 1;
      return Promise.resolve(api.projects);
    },
    // Deliberately leaves `api.projects` alone: the list the wizard refetches
    // after a create has not caught up with it yet.
    createProject: (_agentId: string, body: { name: string; path: string }) => {
      api.projectWrites.push({ name: body.name, path: body.path });
      return Promise.resolve(
        projectInfoSchema.parse({ name: body.name, path: body.path, order: 2 }),
      );
    },
  };
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
  const navigate = useNavigate();
  useEffect(() => {
    visited.push(location.search);
  }, [location]);
  return (
    <>
      <span data-testid="location">{`${location.pathname}${location.search}`}</span>
      {/* The only way to exercise browser Back, which the wizard's own Back
          button deliberately does not imitate. */}
      <button
        type="button"
        data-testid="history-back"
        onClick={() => {
          void navigate(-1);
        }}
      />
    </>
  );
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
  api.projectLists = 0;
  api.projectWrites.length = 0;
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
    // It only highlights. Now that picking a project advances, a pre-fill that
    // went through the same path would launch the wizard past a step the user
    // never saw.
    expect(screen.getByRole('heading', { name: 'Where should it run?' })).toBeInTheDocument();
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

describe('NewSession project step', () => {
  const RECENTS_KEY = 'recent_custom_paths::agent-1';

  it('advances to the agent step on the project row itself', async () => {
    await renderWizard();

    fireEvent.click(screen.getByRole('button', { name: /alpha/ }));

    expect(await screen.findByRole('heading', { name: 'What should it run?' })).toBeInTheDocument();
    expect(currentPath()).toBe('/agents/agent-1/new-session?step=mode&project=alpha');
    // One history write for one click: a replace of this entry followed by a
    // push of the next races the data router's abort rule, and the dropped one
    // would be the replace.
    expect(visited).toEqual(['', '?step=mode&project=alpha']);
  });

  it('brings the picked row back highlighted on the wizard own Back', async () => {
    await renderWizard();
    fireEvent.click(screen.getByRole('button', { name: /alpha/ }));
    await screen.findByRole('heading', { name: 'What should it run?' });

    fireEvent.click(screen.getByRole('button', { name: 'Back' }));

    expect(await screen.findByRole('heading', { name: 'Where should it run?' })).toBeInTheDocument();
    expect(currentPath()).toBe('/agents/agent-1/new-session?step=project&project=alpha');
    expect(screen.getByRole('button', { name: /alpha/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('returns to the project step it came from on browser Back', async () => {
    // The stated cost of the single push: Back undoes the pick rather than
    // preserving it, because the entry it returns to predates the click.
    await renderWizard();
    fireEvent.click(screen.getByRole('button', { name: /alpha/ }));
    await screen.findByRole('heading', { name: 'What should it run?' });

    fireEvent.click(screen.getByTestId('history-back'));

    expect(await screen.findByRole('heading', { name: 'Where should it run?' })).toBeInTheDocument();
    expect(currentPath()).toBe('/agents/agent-1/new-session');
    expect(screen.getByRole('button', { name: /alpha/ })).toHaveAttribute('aria-pressed', 'false');
  });

  it('advances on a recent directory, carrying it in the URL', async () => {
    // Seeded before the render: the recents read is a mount effect.
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(['/srv/two']));
    await renderWizard();

    fireEvent.click(screen.getByRole('button', { name: '/srv/two' }));

    expect(await screen.findByRole('heading', { name: 'What should it run?' })).toBeInTheDocument();
    // `URLSearchParams` form-encodes the separators; `parseWizardLocation`
    // reads them back through the same API.
    expect(currentPath()).toBe('/agents/agent-1/new-session?step=mode&path=%2Fsrv%2Ftwo');
  });

  it('keeps a recent that is not an absolute path on the step, under its error', async () => {
    // Only a hand-edited blob can hold one — `noteRecentPath` is fed cwds the
    // launch already validated — but the guard is what keeps it from flashing
    // the next step and being bounced back by the clamp.
    window.localStorage.setItem(RECENTS_KEY, JSON.stringify(['srv/two']));
    await renderWizard();

    fireEvent.click(screen.getByRole('button', { name: 'srv/two' }));

    expect(screen.getByRole('heading', { name: 'Where should it run?' })).toBeInTheDocument();
    expect(screen.getByLabelText('Absolute path')).toHaveValue('srv/two');
    expect(screen.getByText('Path must be absolute')).toBeInTheDocument();
    expect(visited).toEqual(['']);
  });

  it('still needs Continue for a path typed by hand', async () => {
    await renderWizard();

    fireEvent.change(screen.getByLabelText('Absolute path'), {
      target: { value: '/srv/typed' },
    });
    // Typing never navigates: a `history.replaceState` per character is what
    // browsers rate-limit.
    expect(visited).toEqual(['']);

    fireEvent.click(screen.getByRole('button', { name: 'Continue' }));

    expect(await screen.findByRole('heading', { name: 'What should it run?' })).toBeInTheDocument();
    expect(currentPath()).toBe('/agents/agent-1/new-session?step=mode&path=%2Fsrv%2Ftyped');
  });

  it('leaves Continue disabled on a deep link that already names a project', async () => {
    await renderWizard('?step=project&project=alpha');

    expect(screen.getByRole('button', { name: /alpha/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
  });
});

describe('NewSession create project', () => {
  const openCreateDialog = (): HTMLElement => {
    fireEvent.click(screen.getByRole('button', { name: /Create new project…/ }));
    return screen.getByRole('dialog', { name: 'New project' });
  };

  function saveProject(dialog: HTMLElement, name: string) {
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: name } });
    fireEvent.change(within(dialog).getByLabelText('Path'), { target: { value: `/srv/${name}` } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  }

  it('advances to the agent step with the new project the list has not caught up with', async () => {
    await renderWizard();

    saveProject(openCreateDialog(), 'gamma');

    expect(await screen.findByRole('heading', { name: 'What should it run?' })).toBeInTheDocument();
    expect(api.projectWrites).toEqual([{ name: 'gamma', path: '/srv/gamma' }]);
    // The dialog's refetch has landed and still lacks `gamma`, so only the seed
    // keeps the clamp from sending an unknown `?project=` back to step one.
    expect(api.projectLists).toBe(2);
    expect(
      client.getQueryData<ProjectInfo[]>(queryKeys.projects('agent-1'))?.map((row) => row.name),
    ).toEqual(['alpha', 'beta', 'gamma']);
    expect(currentPath()).toBe('/agents/agent-1/new-session?step=mode&project=gamma');
    expect(visited).toEqual(['', '?step=mode&project=gamma']);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('drops a half-typed one-off path in favour of the created project', async () => {
    await renderWizard();
    fireEvent.change(screen.getByLabelText('Absolute path'), {
      target: { value: '/srv/typed' },
    });

    saveProject(openCreateDialog(), 'gamma');
    await screen.findByRole('heading', { name: 'What should it run?' });
    fireEvent.click(screen.getByRole('button', { name: 'Back' }));

    // Typing never reached the URL, so no navigation would clear the field:
    // left behind, it would outrank the created row and be what Continue sends.
    expect(await screen.findByRole('heading', { name: 'Where should it run?' })).toBeInTheDocument();
    expect(screen.getByLabelText('Absolute path')).toHaveValue('');
    expect(screen.getByRole('button', { name: /gamma/ })).toHaveAttribute('aria-pressed', 'true');
  });

  it('closes on Cancel and leaves the wizard on the project step', async () => {
    await renderWizard();

    fireEvent.click(within(openCreateDialog()).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Where should it run?' })).toBeInTheDocument();
    expect(api.projectWrites).toEqual([]);
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

describe('NewSession capability probe', () => {
  it('drops the capabilities cache with the visit, so re-entry starts from a fresh probe', async () => {
    await renderWizard();
    const key = queryKeys.agentCapabilities('agent-1');
    await waitFor(() => {
      expect(client.getQueryData(key)).toBeDefined();
    });

    cleanup();

    // gcTime: 0 pins the entry to the visit: with a cross-visit cache,
    // `refetchOnMount: 'always'` would serve the previous visit's response
    // while the re-probe is in flight, keeping a since-downgraded agent's
    // stale `model_details` (catalog efforts like `ultra`) selectable and
    // reconciled-to until the refetch lands.
    await waitFor(() => {
      expect(client.getQueryData(key)).toBeUndefined();
    });
  });
});
