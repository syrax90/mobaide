import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resetSingleFlight, singleFlight } from './singleFlight';

/** A task whose completion the test controls, so overlap is deterministic. */
function deferredTask<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  // Settling before any caller awaits is what makes these tests deterministic;
  // this keeps that from surfacing as an unhandled rejection.
  void promise.catch(() => undefined);
  let calls = 0;
  return {
    resolve,
    reject,
    get calls() {
      return calls;
    },
    run: () => {
      calls += 1;
      return promise;
    },
  };
}

describe('singleFlight same-tab dedupe', () => {
  beforeEach(() => {
    resetSingleFlight();
  });

  it('collapses concurrent callers onto one task run', async () => {
    const task = deferredTask<string>();

    const first = singleFlight('refresh', task.run);
    const second = singleFlight('refresh', task.run);
    const third = singleFlight('refresh', task.run);
    task.resolve('token-1');

    expect(await Promise.all([first, second, third])).toEqual([
      'token-1',
      'token-1',
      'token-1',
    ]);
    expect(task.calls).toBe(1);
  });

  it('keeps different keys independent', async () => {
    const agentA = deferredTask<string>();
    const agentB = deferredTask<string>();

    const first = singleFlight('agent-a', agentA.run);
    const second = singleFlight('agent-b', agentB.run);
    agentA.resolve('a');
    agentB.resolve('b');

    expect(await first).toBe('a');
    expect(await second).toBe('b');
  });

  it('releases the slot after a run so the next caller does real work', async () => {
    const first = deferredTask<string>();
    first.resolve('token-1');
    expect(await singleFlight('refresh', first.run)).toBe('token-1');

    const second = deferredTask<string>();
    second.resolve('token-2');

    expect(await singleFlight('refresh', second.run)).toBe('token-2');
    expect(second.calls).toBe(1);
  });

  it('releases the slot after a rejection instead of wedging every retry', async () => {
    const failing = deferredTask<string>();
    failing.reject(new Error('network down'));
    await expect(singleFlight('refresh', failing.run)).rejects.toThrow('network down');

    const retry = deferredTask<string>();
    retry.resolve('token-2');

    expect(await singleFlight('refresh', retry.run)).toBe('token-2');
  });
});

describe('singleFlight cross-tab lock', () => {
  const requested: string[] = [];

  beforeEach(() => {
    resetSingleFlight();
    requested.length = 0;
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: {
        request: async <T>(name: string, callback: () => Promise<T>) => {
          requested.push(name);
          return await callback();
        },
      },
    });
  });

  afterEach(() => {
    // `navigator.locks` is absent in jsdom; put it back that way so the
    // no-Locks fallback keeps being exercised by the suite above.
    Reflect.deleteProperty(navigator, 'locks');
  });

  it('runs the task under a namespaced lock', async () => {
    const task = deferredTask<string>();
    task.resolve('token-1');

    expect(await singleFlight('user-refresh', task.run)).toBe('token-1');
    expect(requested).toEqual(['mobaide-user-refresh']);
  });

  it('short-circuits on recheck without running the task', async () => {
    const task = deferredTask<string>();

    const result = await singleFlight('user-refresh', task.run, {
      recheck: () => 'already-rotated',
    });

    expect(result).toBe('already-rotated');
    expect(task.calls).toBe(0);
  });

  it('runs the task when recheck finds nothing usable', async () => {
    const task = deferredTask<string>();
    task.resolve('token-1');

    const result = await singleFlight('user-refresh', task.run, {
      recheck: () => null,
    });

    expect(result).toBe('token-1');
    expect(task.calls).toBe(1);
  });
});
