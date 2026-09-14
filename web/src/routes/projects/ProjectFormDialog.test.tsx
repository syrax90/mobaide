import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/errors';
import { projectInfoSchema, type ProjectInfo } from '../../protocol/dto/projects';

vi.mock('../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const project = (over: Record<string, unknown> = {}): ProjectInfo =>
  projectInfoSchema.parse({ name: 'proj', path: '/srv/proj', conda_env: 'ml', ...over });

const api = vi.hoisted(() => ({
  updates: [] as { currentName: string; body: Record<string, unknown> }[],
  creates: [] as { name: string; path: string }[],
  /** When set, the POST fails with it instead of answering. */
  createError: null as Error | null,
}));

vi.mock('../../api/projectRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/projectRepository')>();
  return {
    ...actual,
    updateProject: (_agentId: string, currentName: string, body: Record<string, unknown>) => {
      api.updates.push({ currentName, body });
      return Promise.resolve(project({ name: currentName }));
    },
    // Answers with fields the form never sent, so a caller handed the draft
    // instead of the server's reply is visible.
    createProject: (_agentId: string, body: { name: string; path: string }) => {
      api.creates.push({ name: body.name, path: body.path });
      if (api.createError !== null) return Promise.reject(api.createError);
      return Promise.resolve(project({ name: body.name, path: body.path, is_git: true, order: 4 }));
    },
    listLinearLabels: () => Promise.resolve([]),
    listLinearStates: () => Promise.resolve([]),
  };
});

const { ProjectFormDialog } = await import('./ProjectFormDialog');

let client: QueryClient;
let closed: number;
/** `onCreated` and `onClose` in the order the dialog called them. */
const calls: string[] = [];
const received: ProjectInfo[] = [];

const tree = (editing: ProjectInfo | null) => (
  <QueryClientProvider client={client}>
    <ProjectFormDialog
      agentId="agent-1"
      editing={editing}
      onCreated={(created) => {
        calls.push('created');
        received.push(created);
      }}
      onClose={() => {
        calls.push('closed');
        closed += 1;
      }}
    />
  </QueryClientProvider>
);

function fillAndSave(name: string, path: string) {
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: name } });
  fireEvent.change(screen.getByLabelText('Path'), { target: { value: path } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
}

/** The body of the single PATCH the dialog sent. */
const sentBody = (): Record<string, unknown> => api.updates.at(0)?.body ?? {};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.updates.length = 0;
  api.creates.length = 0;
  api.createError = null;
  closed = 0;
  calls.length = 0;
  received.length = 0;
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe('ProjectFormDialog against a live snapshot', () => {
  const before = project();
  const after = project({ path: '/srv/moved', conda_env: 'py311' });

  it('keeps a dirty edit while refreshing the fields the user has not touched', () => {
    const { rerender } = render(tree(before));

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'renamed' } });
    // `projects.changed` lands while the form is open.
    rerender(tree(after));

    expect(screen.getByLabelText('Name')).toHaveValue('renamed');
    expect(screen.getByLabelText('Path')).toHaveValue('/srv/moved');
    expect(screen.getByLabelText('Conda environment (optional)')).toHaveValue('py311');
  });

  it('saves the typed field and none of the ones the push refreshed', async () => {
    const { rerender } = render(tree(before));

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'renamed' } });
    rerender(tree(after));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(api.updates).toHaveLength(1);
    });
    // The refreshed path and conda env equal the snapshot the patch diffs
    // against, so they are absent — the user never asked for them.
    expect(Object.keys(sentBody())).toEqual(['name']);
    expect(sentBody()).toEqual({ name: 'renamed' });
    expect(api.updates.at(0)?.currentName).toBe('proj');
    await waitFor(() => {
      expect(closed).toBe(1);
    });
  });
});

describe('ProjectFormDialog onCreated', () => {
  it('hands over the project the agent saved, then closes', async () => {
    render(tree(null));

    fillAndSave('fresh', '/srv/fresh');

    await waitFor(() => {
      expect(calls).toEqual(['created', 'closed']);
    });
    expect(api.creates).toEqual([{ name: 'fresh', path: '/srv/fresh' }]);
    expect(received).toEqual([
      project({ name: 'fresh', path: '/srv/fresh', is_git: true, order: 4 }),
    ]);
  });

  it('keeps a duplicate name under Name, without reporting a create or closing', async () => {
    const message = "Project 'proj' already exists";
    api.createError = new ApiError({
      status: 409,
      message,
      code: null,
      retryAfterSeconds: null,
      detail: message,
    });
    render(tree(null));

    fillAndSave('proj', '/srv/proj');

    await waitFor(() => {
      expect(screen.getByLabelText('Name')).toHaveAccessibleDescription(message);
    });
    expect(api.creates).toHaveLength(1);
    expect(calls).toEqual([]);
  });

  it('never reports an edit as a create', async () => {
    render(tree(project()));

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'renamed' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(calls).toEqual(['closed']);
    });
    expect(api.updates).toHaveLength(1);
    expect(received).toEqual([]);
  });
});

describe('ProjectFormDialog flags touch gate', () => {
  const before = project({ default_flags: { model: 'opus' } });
  const after = project({ default_flags: { model: 'sonnet' } });

  it('re-seeds an untouched flags editor from the pushed snapshot', () => {
    const { rerender } = render(tree(before));
    expect(screen.getByLabelText('Value')).toHaveValue('opus');

    rerender(tree(after));

    expect(screen.getByLabelText('Value')).toHaveValue('sonnet');
  });

  it('freezes a touched flags editor and saves what the user typed', async () => {
    const { rerender } = render(tree(before));

    fireEvent.change(screen.getByLabelText('Value'), { target: { value: 'haiku' } });
    rerender(tree(after));

    expect(screen.getByLabelText('Value')).toHaveValue('haiku');

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(api.updates).toHaveLength(1);
    });
    expect(sentBody()).toEqual({ default_flags: { model: 'haiku' } });
  });
});
