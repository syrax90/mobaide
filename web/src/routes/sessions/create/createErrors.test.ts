import { describe, expect, it } from 'vitest';
import { toApiError } from '../../../api/errors';
import {
  classifyAutoScanError,
  classifyCreateError,
  codexLoginErrorMessage,
  errorDetailString,
  hasErrorMarker,
} from './createErrors';

const apiError = (status: number, body: unknown) =>
  toApiError(new Response(null, { status }), body);

const CUSTOM_CLI = { cli: 'custom_0123456789ab' };

describe('hasErrorMarker', () => {
  it('finds a marker at both depths by default', () => {
    // The relay puts it top-level; the agent's own route nests it under
    // `detail` via HTTPException.
    expect(hasErrorMarker({ codex_auth_required: true, detail: 'x' }, 'codex_auth_required')).toBe(
      true,
    );
    expect(
      hasErrorMarker({ detail: { codex_auth_required: true } }, 'codex_auth_required'),
    ).toBe(true);
  });

  it('ignores a nested marker when only the top level counts', () => {
    const options = { topLevelOnly: true };

    expect(
      hasErrorMarker({ agent_update_required: true }, 'agent_update_required', options),
    ).toBe(true);
    expect(
      hasErrorMarker({ detail: { agent_update_required: true } }, 'agent_update_required', options),
    ).toBe(false);
  });

  it('requires the marker to be an own key set to true', () => {
    // `constructor` resolves to an inherited function on any plain object, which
    // a bare index would read as truthy.
    expect(hasErrorMarker({}, 'constructor')).toBe(false);
    expect(hasErrorMarker({ codex_auth_required: 'yes' }, 'codex_auth_required')).toBe(false);
    expect(hasErrorMarker('Bad Gateway', 'codex_auth_required')).toBe(false);
    expect(hasErrorMarker([{ codex_auth_required: true }], 'codex_auth_required')).toBe(false);
  });
});

describe('errorDetailString', () => {
  it('reads a string detail at either depth and nothing else', () => {
    expect(errorDetailString({ detail: 'boom' })).toBe('boom');
    expect(errorDetailString({ detail: { detail: 'nested boom' } })).toBe('nested boom');
    expect(errorDetailString({ detail: [{ msg: 'too long' }] })).toBeNull();
    expect(errorDetailString('plain text')).toBeNull();
  });
});

describe('classifyCreateError', () => {
  it('opens the Codex login dialog on the marker, whatever the status', () => {
    // The server answers 409, not 401, so nothing in the auth layer sees it.
    const failure = classifyCreateError(
      apiError(409, { codex_auth_required: true, detail: 'Codex is not signed in.' }),
      { cli: 'codex' },
    );

    expect(failure).toEqual({ kind: 'codexAuthRequired', message: 'Codex is not signed in.' });
  });

  it('detects the Codex marker nested under detail too', () => {
    const failure = classifyCreateError(apiError(409, { detail: { codex_auth_required: true } }), {
      cli: 'codex',
    });

    expect(failure.kind).toBe('codexAuthRequired');
  });

  it('only trusts a top-level agent_update_required', () => {
    const top = classifyCreateError(
      apiError(426, { agent_update_required: true, detail: 'old agent' }),
      CUSTOM_CLI,
    );
    const nested = classifyCreateError(
      apiError(426, { detail: { agent_update_required: true } }),
      CUSTOM_CLI,
    );

    expect(top).toEqual({
      kind: 'agentUpdateRequired',
      message: 'Update your agent to launch custom agent types',
    });
    expect(nested.kind).toBe('generic');
  });

  it('blames the agent type only on the exact 404 detail for a custom launch', () => {
    expect(
      classifyCreateError(apiError(404, { detail: 'Custom agent type not found' }), CUSTOM_CLI),
    ).toEqual({ kind: 'customTypeDeleted', message: 'This agent type was deleted' });
  });

  it('falls through when a custom launch 404s for a deleted project instead', () => {
    const failure = classifyCreateError(
      apiError(404, { detail: "Project 'demo' not found" }),
      CUSTOM_CLI,
    );

    expect(failure).toEqual({
      kind: 'generic',
      message: "Failed to create session: Project 'demo' not found",
    });
  });

  it('falls through when the agent-type 404 arrives on a built-in launch', () => {
    const failure = classifyCreateError(apiError(404, { detail: 'Custom agent type not found' }), {
      cli: 'claude',
    });

    expect(failure.kind).toBe('generic');
  });

  it('reads a 422 validation array rather than printing the status', () => {
    const failure = classifyCreateError(
      apiError(422, { detail: [{ msg: 'branch_name too long' }] }),
      { cli: 'claude' },
    );

    expect(failure.message).toBe('Failed to create session: branch_name too long');
  });

  it('describes a plain transport failure', () => {
    expect(classifyCreateError(new Error('Network down'), { cli: null })).toEqual({
      kind: 'generic',
      message: 'Failed to create session: Network down',
    });
  });
});

describe('classifyAutoScanError', () => {
  it('types the three states the user can act on', () => {
    expect(classifyAutoScanError(apiError(409, { detail: 'busy' }))).toBe(
      'A scan is already running for this project.',
    );
    expect(classifyAutoScanError(apiError(429, { detail: 'slow down' }))).toBe(
      'The agent is at its auto-scan limit.',
    );
    // 404 keeps the server's own copy, which names the missing project.
    expect(classifyAutoScanError(apiError(404, { detail: "Project 'demo' not found" }))).toBe(
      "Project 'demo' not found",
    );
    expect(classifyAutoScanError(apiError(500, { detail: 'boom' }))).toBe(
      'Failed to start Auto Scan: boom',
    );
  });
});

describe('codexLoginErrorMessage', () => {
  it('prefers the server detail and falls back to the status', () => {
    expect(codexLoginErrorMessage(apiError(400, { detail: 'Invalid API key' }))).toBe(
      'Invalid API key',
    );
    expect(codexLoginErrorMessage(apiError(500, null))).toBe('Login failed (HTTP 500)');
    expect(codexLoginErrorMessage(new Error('offline'))).toBe('offline');
  });
});
