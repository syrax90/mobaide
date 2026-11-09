import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ChatStoredSession } from '../../../protocol/dto/chatSessions';

// The collaborator is the repository, not the wire.
const repo = vi.hoisted(() => ({
  rows: [] as ChatStoredSession[],
  calls: [] as { kind: string; cwd: string | undefined }[],
}));

vi.mock('../../../api/chatRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api/chatRepository')>();
  return {
    ...actual,
    listChatSessions: (_agentId: string, kind: string, options: { cwd?: string } = {}) => {
      repo.calls.push({ kind, cwd: options.cwd });
      return Promise.resolve({ sessions: repo.rows, next_cursor: undefined });
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

function renderDialog(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ResumeSessionDialog
        agentId="agent-1"
        kind="antigravity"
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
  repo.calls = [];
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
