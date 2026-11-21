import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ChatStoredSession } from '../../../protocol/dto/chatSessions';

// The collaborator is the repository, not the wire.
const repo = vi.hoisted(() => ({
  rows: [] as ChatStoredSession[],
  canDelete: undefined as boolean | undefined,
  calls: [] as { kind: string; cwd: string | undefined }[],
  deletes: [] as { agentId: string; kind: string; sessionId: string }[],
  deleteError: null as Error | null,
}));

vi.mock('../../../api/chatRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/chatRepository')>();
  return {
    ...actual,
    listChatSessions: (_agentId: string, kind: string, options: { cwd?: string } = {}) => {
      repo.calls.push({ kind, cwd: options.cwd });
      return Promise.resolve({
        sessions: repo.rows,
        next_cursor: undefined,
        can_delete: repo.canDelete,
      });
    },
    deleteChatSession: (agentId: string, kind: string, sessionId: string) => {
      repo.deletes.push({ agentId, kind, sessionId });
      return repo.deleteError === null ? Promise.resolve() : Promise.reject(repo.deleteError);
    },
  };
});

const { ResumeSessionDialog } = await import('./ResumeSessionDialog');

const OPEN_ELSEWHERE = 'Open in another app on your PC. Close it there first.';

function row(
  sessionId: string,
  title: string,
  extra: Pick<ChatStoredSession, 'open_elsewhere' | 'open_session_id'>,
): ChatStoredSession {
  return {
    session_id: sessionId,
    cwd: '/srv/mobaide',
    title,
    updated_at: undefined,
    ...extra,
  };
}

function renderDialog(kind: 'antigravity' | 'claude' | 'codex' = 'antigravity'): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ResumeSessionDialog
        agentId="agent-1"
        kind={kind}
        live={false}
        folder="/srv/mobaide"
        opening={null}
        error={null}
        onPick={() => undefined}
        onClose={() => undefined}
      />
    </QueryClientProvider>,
  );
}

/** The row's own button: its title, folder and note share one accessible name. */
const rowButton = (title: string): HTMLElement => {
  const button = screen.getByText(title).closest('button');
  if (button === null) throw new Error(`no row for ${title}`);
  return button;
};

afterEach(() => {
  cleanup();
  repo.rows = [];
  repo.canDelete = undefined;
  repo.calls = [];
  repo.deletes = [];
  repo.deleteError = null;
});

describe('ResumeSessionDialog — Antigravity', () => {
  it('warns on a row another app on the PC has open, but not on one Mobaide already shows', async () => {
    repo.rows = [
      row('in-agy', 'Write release notes', { open_elsewhere: true, open_session_id: undefined }),
      row('in-mobaide', 'Fix flaky upload test', {
        open_elsewhere: true,
        open_session_id: 'mob-7',
      }),
      row('closed', 'Refactor the login flow', {
        open_elsewhere: false,
        open_session_id: undefined,
      }),
      row('old-agent', 'Explain the cache layer', {
        open_elsewhere: undefined,
        open_session_id: undefined,
      }),
    ];

    renderDialog();
    await screen.findByText('Write release notes');

    expect(rowButton('Write release notes')).toHaveTextContent(OPEN_ELSEWHERE);
    expect(rowButton('Fix flaky upload test')).not.toHaveTextContent(OPEN_ELSEWHERE);
    expect(rowButton('Fix flaky upload test')).toHaveTextContent('Open');
    expect(rowButton('Refactor the login flow')).not.toHaveTextContent(OPEN_ELSEWHERE);
    expect(rowButton('Explain the cache layer')).not.toHaveTextContent(OPEN_ELSEWHERE);
    expect(screen.getAllByText(OPEN_ELSEWHERE)).toHaveLength(1);
    expect(repo.calls).toEqual([{ kind: 'antigravity', cwd: '/srv/mobaide' }]);
  });
});

const closed = { open_elsewhere: false, open_session_id: undefined } as const;

describe('ResumeSessionDialog — Delete', () => {
  it('offers Delete only on rows that nothing has open, and only when the agent can delete', async () => {
    repo.canDelete = true;
    repo.rows = [
      row('closed', 'Refactor the login flow', closed),
      row('in-mobaide', 'Fix flaky upload test', { open_elsewhere: false, open_session_id: 'mob-7' }),
      row('in-agy', 'Write release notes', { open_elsewhere: true, open_session_id: undefined }),
    ];

    renderDialog();
    await screen.findByText('Refactor the login flow');

    expect(screen.getAllByRole('button', { name: /^Delete / }).map((b) => b.getAttribute('aria-label'))).toEqual([
      'Delete Refactor the login flow',
    ]);
  });

  it('offers no Delete when an older agent does not send can_delete', async () => {
    repo.rows = [row('closed', 'Refactor the login flow', closed)];

    renderDialog('claude');
    await screen.findByText('Refactor the login flow');

    expect(screen.queryByRole('button', { name: /^Delete / })).toBeNull();
  });

  it('deletes after the confirm and removes the row in place', async () => {
    repo.canDelete = true;
    repo.rows = [
      row('a', 'Refactor the login flow', closed),
      row('b', 'Fix flaky upload test', closed),
    ];

    renderDialog('codex');
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Refactor the login flow' }));
    const confirm = screen.getByRole('alertdialog', { name: 'Delete this session?' });
    expect(confirm).toHaveTextContent('Codex moves this session to its archive');
    expect(repo.deletes).toEqual([]);
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));

    await screen.findByText('Fix flaky upload test');
    expect(await screen.findByRole('button', { name: 'Delete Fix flaky upload test' })).toBeInTheDocument();
    expect(screen.queryByText('Refactor the login flow')).toBeNull();
    expect(repo.deletes).toEqual([{ agentId: 'agent-1', kind: 'codex', sessionId: 'a' }]);
    expect(repo.calls).toHaveLength(1);
  });

  it('shows the agent detail when the delete fails and keeps the row', async () => {
    repo.canDelete = true;
    repo.deleteError = new Error('This session is open in a Mobaide chat. Close the chat first.');
    repo.rows = [row('a', 'Refactor the login flow', closed)];

    renderDialog('claude');
    fireEvent.click(await screen.findByRole('button', { name: 'Delete Refactor the login flow' }));
    const confirm = screen.getByRole('alertdialog', { name: 'Delete this session?' });
    expect(confirm).toHaveTextContent('You cannot undo this.');
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This session is open in a Mobaide chat. Close the chat first.',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Keep' }));
    expect(await screen.findByText('Refactor the login flow')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete Refactor the login flow' })).toBeInTheDocument();
  });
});
