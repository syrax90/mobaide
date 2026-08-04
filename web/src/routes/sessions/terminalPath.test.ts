import { describe, expect, it } from 'vitest';
import { newSessionPath, terminalPath } from './terminalPath';

describe('terminalPath', () => {
  it('builds the terminal route for a session', () => {
    expect(terminalPath('agent-1', 'session-1')).toBe('/agents/agent-1/sessions/session-1');
  });

  it('escapes ids that would otherwise change the shape of the path', () => {
    // Both ids are server-generated, but a `/` would silently retarget the
    // navigation at a different route.
    expect(terminalPath('a/b', 'x y')).toBe('/agents/a%2Fb/sessions/x%20y');
  });
});

describe('newSessionPath', () => {
  it('builds the wizard route and escapes the agent id', () => {
    expect(newSessionPath('agent-1')).toBe('/agents/agent-1/new-session');
    expect(newSessionPath('a/b')).toBe('/agents/a%2Fb/new-session');
  });
});
