import { afterEach, describe, expect, it } from 'vitest';
import { sessionResponseSchema, type SessionResponse } from '../../protocol/dto/session';
import {
  claimAutoResumeAttempt,
  decideAutoResume,
  resetAutoResumeAttempts,
} from './autoResume';

const session = (sessionId: string, state: string): SessionResponse =>
  sessionResponseSchema.parse({
    session_id: sessionId,
    state,
    created_at: '2026-08-04T10:00:00Z',
  });

afterEach(() => {
  resetAutoResumeAttempts();
});

describe('decideAutoResume', () => {
  it('resumes a session that is still alive', () => {
    expect(decideAutoResume([session('s1', 'running')], 's1')).toEqual({
      kind: 'resume',
      sessionId: 's1',
    });
    expect(decideAutoResume([session('s1', 'starting')], 's1')).toEqual({
      kind: 'resume',
      sessionId: 's1',
    });
  });

  it.each(['stopped', 'crashed'])(
    'forgets a %s session that is present in the list',
    (state) => {
      expect(decideAutoResume([session('s1', state)], 's1')).toEqual({ kind: 'clear' });
    },
  );

  it('does nothing — and never clears — when the id is absent from the list', () => {
    // A truncated list, or one from an agent still enumerating, is not proof
    // the session is gone. Clearing here would silently lose the resume target.
    expect(decideAutoResume([session('other', 'running')], 's1')).toEqual({ kind: 'none' });
  });

});

describe('claimAutoResumeAttempt', () => {
  it('grants one attempt per agent, and only one', () => {
    // The second call is the Back-button case: the route unmounts on navigate
    // and mounts again, which would otherwise trap the user in a loop.
    expect(claimAutoResumeAttempt('agent-a')).toBe(true);
    expect(claimAutoResumeAttempt('agent-a')).toBe(false);
  });

  it('grants a separate attempt to each agent', () => {
    claimAutoResumeAttempt('agent-a');

    expect(claimAutoResumeAttempt('agent-b')).toBe(true);
    // Switching back must not re-navigate either.
    expect(claimAutoResumeAttempt('agent-a')).toBe(false);
  });

  it('re-arms after a reset, the analogue of a page reload', () => {
    claimAutoResumeAttempt('agent-a');
    resetAutoResumeAttempts();

    expect(claimAutoResumeAttempt('agent-a')).toBe(true);
  });
});
