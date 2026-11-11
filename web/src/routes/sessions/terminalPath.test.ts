import { describe, expect, it } from 'vitest';
import { newSessionPath, terminalPath } from './terminalPath';

describe('terminalPath', () => {
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
