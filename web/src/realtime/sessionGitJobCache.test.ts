import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { SessionGitJob } from '../api/sessionGitRepository';
import { queryKeys } from '../lib/queryKeys';
import {
  applySessionGitJob,
  applySessionGitJobEvent,
  type SessionGitLatestJob,
} from './sessionGitJobCache';

const AGENT = 'agent-1';
const jobKey = (jobId: string) => queryKeys.sessionGitJob(AGENT, 's1', jobId);
const latestKey = queryKeys.sessionGitLatestJob(AGENT, 's1');

const job = (over: Partial<SessionGitJob> = {}): SessionGitJob => ({
  job_id: 'j1',
  session_id: 's1',
  op: 'push',
  status: 'running',
  phase: null,
  percent: null,
  line: null,
  ok: null,
  error_code: null,
  message: null,
  ...over,
});

const cachedJob = (jobId = 'j1'): SessionGitJob | undefined =>
  client.getQueryData<SessionGitJob>(jobKey(jobId));
const latest = (): SessionGitLatestJob | undefined =>
  client.getQueryData<SessionGitLatestJob>(latestKey);

let client: QueryClient;

beforeEach(() => {
  client = new QueryClient();
});

afterEach(() => {
  client.clear();
});

describe('applySessionGitJob', () => {
  it('writes the job under its id and points the session at it', () => {
    applySessionGitJob(client, AGENT, job());

    expect(cachedJob()).toEqual(job());
    expect(latest()).toEqual({ jobId: 'j1' });
  });

  it('lets a later running frame replace an earlier one', () => {
    applySessionGitJob(client, AGENT, job({ percent: 10, phase: 'Counting objects' }));
    applySessionGitJob(client, AGENT, job({ percent: 40, phase: 'Writing objects' }));

    expect(cachedJob()).toMatchObject({ percent: 40, phase: 'Writing objects' });
  });

  it('never lets a running frame overwrite a done one', () => {
    // The reconcile poll and the frames race: a GET issued just before `done`
    // landed answers after it, still saying `running`.
    const done = job({ status: 'done', ok: true, percent: 100, message: 'main -> main' });
    applySessionGitJob(client, AGENT, done);

    applySessionGitJob(client, AGENT, job({ percent: 99 }));

    expect(cachedJob()).toEqual(done);
  });

  it('keeps the pointer identity for the same job and moves it to a newer one', () => {
    applySessionGitJob(client, AGENT, job());
    const first = latest();

    applySessionGitJob(client, AGENT, job({ percent: 5 }));
    expect(latest()).toBe(first);

    applySessionGitJob(client, AGENT, job({ job_id: 'j2', op: 'fetch' }));
    expect(latest()).toEqual({ jobId: 'j2' });
    expect(cachedJob('j1')).toEqual(job({ percent: 5 }));
    expect(cachedJob('j2')).toMatchObject({ op: 'fetch' });
  });

  it('keeps sessions apart', () => {
    applySessionGitJob(client, AGENT, job({ session_id: 's2', job_id: 'j9' }));

    expect(latest()).toBeUndefined();
    expect(cachedJob('j9')).toBeUndefined();
    expect(client.getQueryData(queryKeys.sessionGitLatestJob(AGENT, 's2'))).toEqual({ jobId: 'j9' });
    expect(client.getQueryData(queryKeys.sessionGitJob(AGENT, 's2', 'j9'))).toMatchObject({
      job_id: 'j9',
    });
  });
});

describe('applySessionGitJobEvent', () => {
  it('applies a wire frame with its nulls dropped', () => {
    const outcome = applySessionGitJobEvent(client, AGENT, {
      ...job(),
      phase: 'Writing objects',
      percent: 45,
      line: 'Writing objects:  45% (14/32)',
    });

    expect(outcome).toBe('applied');
    expect(cachedJob()).toEqual({
      job_id: 'j1',
      session_id: 's1',
      op: 'push',
      status: 'running',
      phase: 'Writing objects',
      percent: 45,
      line: 'Writing objects:  45% (14/32)',
    });
    expect(latest()).toEqual({ jobId: 'j1' });
  });

  it('applies a done frame over the running one', () => {
    applySessionGitJobEvent(client, AGENT, job({ percent: 45 }));

    applySessionGitJobEvent(client, AGENT, {
      ...job(),
      status: 'done',
      ok: false,
      error_code: 'rejected',
      message: '! [rejected] main -> main (fetch first)',
    });

    expect(cachedJob()).toMatchObject({ status: 'done', ok: false, error_code: 'rejected' });
  });

  it.each([
    ['an op it cannot name', { op: 'clone' }],
    ['a status it cannot name', { status: 'queued' }],
    ['no job id', { job_id: undefined }],
    ['no session', { session_id: null }],
  ])('drops a frame with %s and writes nothing', (_case, over) => {
    expect(applySessionGitJobEvent(client, AGENT, { ...job(), ...over })).toBe('dropped');

    expect(client.getQueryCache().getAll()).toEqual([]);
  });
});
