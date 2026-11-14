import { describe, expect, it } from 'vitest';
import { VOICE_CODE } from './voiceApi';
import { cleanupErrorCopy } from './voiceErrorCopy';

describe('cleanupErrorCopy', () => {
  it.each([
    ['claude', 'Claude Code', 'claude update'],
    ['cursor', 'Cursor', 'cursor-agent update'],
  ])('names the AI agent of a %s session and how to update it', (kind, name, update) => {
    expect(cleanupErrorCopy(VOICE_CODE.cleanupUnavailable, 'laptop', kind)).toBe(
      `${name} isn't installed on laptop.`,
    );
    expect(cleanupErrorCopy(VOICE_CODE.cleanupOutdated, 'laptop', kind)).toBe(
      `${name} on laptop is too old. Run "${update}" there.`,
    );
    expect(cleanupErrorCopy(VOICE_CODE.cleanupTimeout, 'laptop', kind)).toBe(`${name} took too long.`);
    expect(cleanupErrorCopy(VOICE_CODE.cleanupFailed, 'laptop', kind)).toBe(`${name} reported an error.`);
  });

  it.each([undefined, 'constructor'])('stays generic for a kind it cannot name (%s)', (kind) => {
    expect(cleanupErrorCopy(VOICE_CODE.cleanupOutdated, 'laptop', kind)).toBe(
      "The session's AI agent on laptop is too old. Update it there.",
    );
    expect(cleanupErrorCopy(VOICE_CODE.cleanupFailed, 'laptop', kind)).toBe(
      "The session's AI agent reported an error.",
    );
  });

  it('keeps the kind-free reasons', () => {
    expect(cleanupErrorCopy(VOICE_CODE.cleanupBusy, 'laptop', 'codex')).toBe('Another cleanup was still running.');
    expect(cleanupErrorCopy(VOICE_CODE.network, 'laptop', 'codex')).toBe('The connection dropped.');
  });
});
