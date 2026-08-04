import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PromptTemplate } from '../../api/templateRepository';

// The picker's collaborator is the repository, not the wire: what these tests
// need is to hold the PATCH — and the refetch behind it — open, which is a
// layer above `relayClient`. `groupTemplates` stays real, because it decides
// which section a row lands in and a flipped favourite moves.
const repo = vi.hoisted(() => ({
  templates: [] as PromptTemplate[],
  patches: [] as { templateId: string; isFavorite: boolean }[],
  settleFavorite: undefined as ((outcome: 'ok' | 'failed') => void) | undefined,
  listGate: undefined as Promise<void> | undefined,
}));

vi.mock('../../api/templateRepository', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/templateRepository')>();
  return {
    ...actual,
    listTemplates: async () => {
      // Armed only by the test that needs the refetch observable mid-flight.
      if (repo.listGate !== undefined) await repo.listGate;
      return repo.templates;
    },
    touchTemplate: () => Promise.resolve(),
    setTemplateFavorite: (templateId: string, isFavorite: boolean) => {
      repo.patches.push({ templateId, isFavorite });
      return new Promise<void>((resolve, reject) => {
        repo.settleFavorite = (outcome) => {
          if (outcome === 'ok') resolve();
          else reject(new Error('PATCH refused'));
        };
      });
    },
  };
});

const { TemplatesPicker } = await import('./TemplatesPicker');

const template = (name: string): PromptTemplate => ({
  templateId: `id-${name}`,
  name,
  body: `body of ${name}`,
  isFavorite: false,
  lastUsedAt: null,
});

/** One row's star, whichever section the grouping put the row in. */
const star = (name: string): HTMLElement =>
  screen.getByRole('button', {
    name: new RegExp(`(Add|Remove) ${name} (to|from) favourites`),
  });

/**
 * Advance one notification round.
 *
 * React Query schedules its observer notifications on a timer, so nothing a
 * mutation does is visible to the DOM in the same task. Waiting a macrotask is
 * enough for a round already queued; a chain of them wants `waitFor`.
 */
const flush = async (): Promise<void> => {
  await act(async () => {
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  });
};

/** Press a star and let the pending render — the guard itself — land. */
async function clickStar(name: string): Promise<void> {
  fireEvent.click(star(name));
  await flush();
}

/** Wait for a star to come back under the user's hand. */
async function starEnabled(name: string): Promise<void> {
  await waitFor(() => {
    expect(star(name)).toBeEnabled();
  });
}

/** Hold the refetch a successful PATCH triggers open; the return opens it. */
function holdList(): () => void {
  let open = (): void => undefined;
  repo.listGate = new Promise<void>((resolve) => {
    open = (): void => {
      resolve();
    };
  });
  return open;
}

/** Settle the PATCH in flight, rather than passing quietly on a stale one. */
async function settlePatch(outcome: 'ok' | 'failed'): Promise<void> {
  const settle = repo.settleFavorite;
  if (settle === undefined) throw new Error('no favourite PATCH is in flight');
  repo.settleFavorite = undefined;
  settle(outcome);
  await flush();
}

async function renderPicker(...templates: PromptTemplate[]): Promise<void> {
  repo.templates = templates;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TemplatesPicker onPick={() => undefined} onClose={() => undefined} />
    </QueryClientProvider>,
  );
  await screen.findByRole('heading', { name: 'All' });
}

beforeEach(() => {
  repo.templates = [];
  repo.patches.length = 0;
  repo.settleFavorite = undefined;
  repo.listGate = undefined;
});

afterEach(cleanup);

describe('TemplatesPicker favourite guard', () => {
  it('disables only the star whose PATCH is in flight', async () => {
    await renderPicker(template('alpha'), template('beta'));

    await clickStar('alpha');

    expect(star('alpha')).toBeDisabled();
    expect(star('beta')).toBeEnabled();
  });

  it('drops a second click inside the round trip instead of sending the same value twice', async () => {
    // The PATCH carries an *absolute* `is_favorite`, computed from the value on
    // screen — and that value only moves once the refetch lands. Both clicks
    // would therefore send `true`, and the user's second toggle would be lost
    // against persisted state rather than merely raced.
    await renderPicker(template('alpha'));

    await clickStar('alpha');
    await clickStar('alpha');

    expect(repo.patches).toEqual([{ templateId: 'id-alpha', isFavorite: true }]);
  });

  it('holds the star until the refetch carrying the new value lands', async () => {
    // The window the guard has to span is wider than the PATCH: until the
    // invalidation refetches, the row still renders the old boolean, so a click
    // landing between the 200 and the fresh list would compute `true` again.
    await renderPicker(template('alpha'));
    await clickStar('alpha');
    const openList = holdList();
    repo.templates = [{ ...template('alpha'), isFavorite: true }];

    await settlePatch('ok');

    expect(star('alpha')).toBeDisabled();

    openList();
    await starEnabled('alpha');

    expect(star('alpha')).toHaveAttribute('aria-pressed', 'true');
  });

  it('hands the star back when the PATCH fails, so the toggle can be retried', async () => {
    // No refetch follows a failure, so nothing else would ever clear the guard.
    await renderPicker(template('alpha'));
    await clickStar('alpha');

    await settlePatch('failed');
    await starEnabled('alpha');

    await clickStar('alpha');
    expect(repo.patches).toHaveLength(2);
  });
});
