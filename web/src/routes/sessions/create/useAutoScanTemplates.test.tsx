import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AutoScanTemplate } from '../../../api/autoscanRepository';
import { ApiError } from '../../../api/errors';

// The collaborator is the repository: the list read answers `api.saved`, and
// a delete fails with `api.deleteError` when one is set.
const api = vi.hoisted(() => ({
  saved: [] as AutoScanTemplate[],
  deleteError: null as ApiError | null,
}));

vi.mock('../../../api/autoscanRepository', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../api/autoscanRepository')>()),
  listAutoScanTemplates: (): Promise<AutoScanTemplate[]> => Promise.resolve(api.saved),
  deleteAutoScanTemplate: (_agentId: string, templateId: string): Promise<AutoScanTemplate[]> =>
    api.deleteError !== null
      ? Promise.reject(api.deleteError)
      : Promise.resolve(api.saved.filter((template) => template.id !== templateId)),
}));

const { useAutoScanTemplates } = await import('./useAutoScanTemplates');

const template = (id: string, name: string): AutoScanTemplate => ({
  id,
  name,
  updated_at: '2026-11-19T10:00:00+00:00',
  settings: { source: 'github', cli: 'codex' },
});
const SMALL_BUGS = template('t1', 'Small bugs');
const COMPLEX = template('t2', 'Complex');

const refused = (status: number, detail: string) =>
  new ApiError({ status, message: detail, code: null, retryAfterSeconds: null, detail, body: { detail } });

let client: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

async function renderLoaded() {
  const hook = renderHook(() => useAutoScanTemplates('a1', true), { wrapper });
  await waitFor(() => {
    expect(hook.result.current.status).toBe('ready');
  });
  return hook;
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  api.saved = [SMALL_BUGS, COMPLEX];
  api.deleteError = null;
});

describe('useAutoScanTemplates — delete', () => {
  // Another client deleted it first: the PC answers 404 and the list must
  // not keep a template that is gone.
  it('drops a template the PC no longer has, without an error', async () => {
    api.deleteError = refused(404, "Template 't1' not found");
    const { result } = await renderLoaded();

    const left = await result.current.remove('t1');

    expect(left).toEqual([COMPLEX]);
    await waitFor(() => {
      expect(result.current.templates).toEqual([COMPLEX]);
    });
  });

  it('keeps the list and rejects with the PC’s sentence on any other failure', async () => {
    api.deleteError = refused(502, 'The PC is offline.');
    const { result } = await renderLoaded();

    await expect(result.current.remove('t1')).rejects.toThrow('The PC is offline.');

    expect(result.current.templates).toEqual([SMALL_BUGS, COMPLEX]);
  });
});
