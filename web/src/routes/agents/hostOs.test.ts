import { describe, expect, it } from 'vitest';
import { AGENT_HOST_OSES, envSnippet } from './hostOs';

describe('envSnippet', () => {
  it('emits exactly the two .env lines the agent parser reads', () => {
    // This is pasted verbatim into the agent's `.env`, which is a plain
    // `KEY=VALUE` file: no `export `, no quoting, no trailing newline, one
    // `\n` between the two keys. Any decoration here silently produces an
    // agent that boots with no credentials.
    expect(envSnippet('agent-1', 'tok-1')).toBe(
      'RELAY_AGENT_ID=agent-1\nRELAY_AGENT_TOKEN=tok-1',
    );
  });

});

describe('AGENT_HOST_OSES', () => {
  it('uses backslashes for the Windows path and none for the POSIX ones', () => {
    // A `String.raw` literal is what keeps `C:\Program Files\…` from becoming
    // `C:Program FilesMobaideAgent…` — the failure mode is invisible in the
    // source and fatal in the paste.
    const byId = Object.fromEntries(AGENT_HOST_OSES.map((os) => [os.id, os.envPath]));

    expect(byId['windows']).toBe(String.raw`C:\Program Files\MobaideAgent\mobaide_agent\.env`);
    expect(byId['linux']).not.toContain('\\');
    expect(byId['macos']).not.toContain('\\');
  });
});
