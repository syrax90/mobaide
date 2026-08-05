import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { projectInfoSchema, type ProjectInfo } from '../../protocol/dto/projects';

vi.mock('../../auth/agentTokens', () => ({
  ensureAgentToken: () => Promise.resolve(null),
  clearAgentToken: () => undefined,
}));

const project = (over: Record<string, unknown> = {}): ProjectInfo =>
  projectInfoSchema.parse({ name: 'proj', path: '/srv/proj', conda_env: 'ml', ...over });

const api = vi.hoisted(() => ({
  updates: [] as { currentName: string; body: Record<string, unknown> }[],
}));

vi.mock('../../api/projectRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/projectRepository')>();
  return {
    ...actual,
    updateProject: (_agentId: string, currentName: string, body: Record<string, unknown>) => {
      api.updates.push({ currentName, body });
      return Promise.resolve(project({ name: currentName }));
    },
    createProject: () => Promise.resolve(project()),
    listLinearLabels: () => Promise.resolve([]),
    listLinearStates: () => Promise.resolve([]),
  };
});

const { ProjectFormDialog } = await import('./ProjectFormDialog');

let client: QueryClient;
let closed: number;

const tree = (editing: ProjectInfo) => (
  <QueryClientProvider client={client}>
    <ProjectFormDialog
      agentId="agent-1"
      editing={editing}
      onClose={() => {
        closed += 1;
      }}
    />
  </QueryClientProvider>
);

/** The body of the single PATCH the dialog sent. */
const sentBody = (): Record<string, unknown> => api.updates.at(0)?.body ?? {};

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.updates.length = 0;
  closed = 0;
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
