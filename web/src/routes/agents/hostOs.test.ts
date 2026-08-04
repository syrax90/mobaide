import { describe, expect, it } from 'vitest';
import { AGENT_HOST_OSES, DEFAULT_AGENT_HOST_OS, envSnippet } from './hostOs';

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

  it('does not quote or escape a token, whatever it contains', () => {
    // Relay tokens are base64url, so `-` and `_` appear routinely and neither
    // needs escaping in a dotenv value.
    expect(envSnippet('agent-1', 'ab-cd_ef.gh')).toContain(
      'RELAY_AGENT_TOKEN=ab-cd_ef.gh',
    );
  });
});

describe('AGENT_HOST_OSES', () => {
  it('offers Linux, macOS and Windows in the mobile order', () => {
    // The order is parity-relevant, not cosmetic: Android's `AgentHostOs`
    // enum and iOS's `AgentHostOS` both declare Linux first, and the default
    // below is `[0]`.
    expect(AGENT_HOST_OSES.map((option) => option.id)).toEqual([
      'linux',
      'macos',
      'windows',
    ]);
  });

  it('defaults to Linux', () => {
    expect(DEFAULT_AGENT_HOST_OS.id).toBe('linux');
  });

  it('gives every OS a distinct absolute .env path and a restart command', () => {
    const paths = AGENT_HOST_OSES.map((option) => option.envPath);

    expect(new Set(paths).size).toBe(AGENT_HOST_OSES.length);
    for (const option of AGENT_HOST_OSES) {
      // Absolute on both separators — the panel says "default install", and a
      // relative path would be meaningless on a host the user is not in.
      expect(option.envPath.startsWith('/') || /^[A-Z]:\\/.test(option.envPath)).toBe(true);
      expect(option.envPath.endsWith('.env')).toBe(true);
      expect(option.restartCommand).not.toBe('');
      expect(option.label).not.toBe('');
    }
  });

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
