import { afterEach, describe, expect, it } from 'vitest';
import { ApiError } from '../api/errors';
import { useToastStore } from '../stores/toastStore';
import { queryClient } from './queryClient';

// A 4xx, so the production `retry` settles the query on its first failure.
const refused = new ApiError({
  status: 409,
  message: 'relay refused',
  code: null,
  retryAfterSeconds: null,
  detail: null,
});

const toasts = (): string[] => useToastStore.getState().toasts.map((toast) => toast.message);

afterEach(() => {
  queryClient.clear();
  useToastStore.setState({ toasts: [] });
});

describe('queryClient error toast', () => {
  it('announces a failed query by its API message', async () => {
    await expect(
      queryClient.fetchQuery({ queryKey: ['loud'], queryFn: () => Promise.reject(refused) }),
    ).rejects.toBe(refused);

    expect(toasts()).toEqual(['relay refused']);
  });

  it('stays quiet for a query that renders its own failure inline', async () => {
    // `meta.silent` is the opt-out for a surface on the top layer, where the
    // toast viewport paints underneath and would never be seen.
    await expect(
      queryClient.fetchQuery({
        queryKey: ['silent'],
        queryFn: () => Promise.reject(refused),
        meta: { silent: true },
      }),
    ).rejects.toBe(refused);

    expect(toasts()).toEqual([]);
  });
});
