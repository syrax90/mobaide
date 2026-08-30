import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PromptTemplate } from '../../api/templateRepository';

// The collaborator is the repository, not the wire. `groupForManagement` stays
// real: it decides which section a row lands in, and a flipped favourite moves.
const api = vi.hoisted(() => ({
  templates: [] as PromptTemplate[],
  creates: [] as { name: string; body: string }[],
  updates: [] as { templateId: string; patch: Record<string, unknown> }[],
  deletes: [] as string[],
  favorites: [] as { templateId: string; isFavorite: boolean }[],
  saveError: null as Error | null,
}));

vi.mock('../../api/templateRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/templateRepository')>();
  return {
    ...actual,
    listTemplates: () => Promise.resolve(api.templates.map((row) => ({ ...row }))),
    createTemplate: (name: string, body: string) => {
      api.creates.push({ name, body });
      return api.saveError === null ? Promise.resolve() : Promise.reject(api.saveError);
    },
    updateTemplate: (templateId: string, patch: Record<string, unknown>) => {
      api.updates.push({ templateId, patch });
      return api.saveError === null ? Promise.resolve() : Promise.reject(api.saveError);
    },
    deleteTemplate: (templateId: string) => {
      api.deletes.push(templateId);
      return Promise.resolve();
    },
    setTemplateFavorite: (templateId: string, isFavorite: boolean) => {
      api.favorites.push({ templateId, isFavorite });
      const row = api.templates.find((entry) => entry.templateId === templateId);
      if (row !== undefined) row.isFavorite = isFavorite;
      return Promise.resolve();
    },
  };
});

const { TemplatesScreen } = await import('./TemplatesScreen');

const template = (name: string): PromptTemplate => ({
  templateId: `id-${name}`,
  name,
  body: `body of ${name}`,
  isFavorite: false,
  lastUsedAt: null,
});

let client: QueryClient;

const renderScreen = () =>
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/templates']}>
        <TemplatesScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.templates = [];
  api.creates.length = 0;
  api.updates.length = 0;
  api.deletes.length = 0;
  api.favorites.length = 0;
  api.saveError = null;
});

afterEach(() => {
  cleanup();
  client.clear();
});

describe('TemplatesScreen editor', () => {
  it('still opens a new template with an empty body', async () => {
    // `TemplateEditorDialog` grew an `initialBody` for the terminal's picker,
    // which pre-fills it from the xterm selection. This caller passes none, so
    // the `??` chain must still fall through to the empty draft.
    renderScreen();
    await screen.findByRole('button', { name: 'New template' });

    fireEvent.click(screen.getByRole('button', { name: 'New template' }));

    expect(screen.getByRole('heading', { name: 'New template' })).toBeInTheDocument();
    expect(screen.getByLabelText('Prompt body')).toHaveValue('');
  });

  it('trims the name but sends the body exactly as typed', async () => {
    renderScreen();
    await screen.findByRole('button', { name: 'New template' });

    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: '  Standup  ' } });
    // Trailing whitespace is load-bearing: the body reaches a PTY unchanged.
    fireEvent.change(screen.getByLabelText('Prompt body'), {
      target: { value: '  git status\n' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(api.creates).toEqual([{ name: 'Standup', body: '  git status\n' }]);
    });
    await waitFor(() => {
      expect(screen.queryByRole('dialog')).toBeNull();
    });
  });

  it('keeps the dialog and the draft when the save fails', async () => {
    api.saveError = new Error('relay refused');
    renderScreen();
    await screen.findByRole('button', { name: 'New template' });

    fireEvent.click(screen.getByRole('button', { name: 'New template' }));
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Standup' } });
    fireEvent.change(screen.getByLabelText('Prompt body'), { target: { value: 'git status' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Failed to save template')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByLabelText('Prompt body')).toHaveValue('git status');
  });

  it('opens an existing row prefilled and patches it', async () => {
    api.templates = [template('alpha')];
    renderScreen();

    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    expect(screen.getByLabelText('Name')).toHaveValue('alpha');

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: ' alpha 2 ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(api.updates).toEqual([
        { templateId: 'id-alpha', patch: { name: 'alpha 2', body: 'body of alpha' } },
      ]);
    });
  });
});

describe('TemplatesScreen row actions', () => {
  it('deletes only after the confirmation naming the template', async () => {
    api.templates = [template('alpha')];
    renderScreen();

    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));

    const confirm = screen.getByRole('alertdialog');
    expect(within(confirm).getByText(/alpha/)).toBeInTheDocument();
    expect(api.deletes).toEqual([]);

    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(api.deletes).toEqual(['id-alpha']);
    });
    await waitFor(() => {
      expect(screen.queryByRole('alertdialog')).toBeNull();
    });
  });

  it('flips a favourite and shows the new state once the server has it', async () => {
    api.templates = [template('alpha')];
    renderScreen();

    fireEvent.click(await screen.findByRole('button', { name: 'Favorite alpha' }));

    await waitFor(() => {
      expect(api.favorites).toEqual([{ templateId: 'id-alpha', isFavorite: true }]);
    });
    expect(await screen.findByRole('button', { name: 'Unfavorite alpha' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});
