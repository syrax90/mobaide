import { describe, expect, it } from 'vitest';
import { autoScanListResponseSchema, parseScanPayload } from './autoscan';

describe('parseScanPayload', () => {
  it('decodes each modelled payload under its own type', () => {
    expect(parseScanPayload('log', { line: 'building' })).toEqual({
      type: 'log',
      line: 'building',
      level: 'info',
      ansi: false,
    });
    expect(parseScanPayload('worktree', { identifier: 'ISS-1', path: '/w' })).toMatchObject({
      type: 'worktree',
      path: '/w',
    });
  });

  it('never lets the payload choose its own branch', () => {
    // The schemas are `looseObject`, so an unknown `type` key survives parsing.
    // Spreading it over the caller's validated `type` would hand an `issue`
    // payload to the `log` arm — and `undefined` where a line of text belongs,
    // which crashes the render that consumes it.
    const decoded = parseScanPayload('issue', {
      identifier: 'ISS-1',
      step: 'state_update',
      status: 'ok',
      type: 'log',
      ansi: true,
    });

    expect(decoded.type).toBe('issue');
  });

  it('degrades an unmodelled type and a malformed payload to unknown', () => {
    expect(parseScanPayload('mystery', {})).toEqual({ type: 'unknown' });
    expect(parseScanPayload('log', { line: 42 })).toEqual({ type: 'unknown' });
    // `type` is wire data: an `Object.prototype` key must not resolve to an
    // inherited function and throw out of the socket listener.
    expect(parseScanPayload('constructor', {})).toEqual({ type: 'unknown' });
    expect(parseScanPayload('__proto__', {})).toEqual({ type: 'unknown' });
  });
});

describe('autoScanListResponseSchema', () => {
  it('materialises the defaults the relay body relies on', () => {
    expect(autoScanListResponseSchema.parse({})).toEqual({
      scans: [],
      capacity: 0,
      max_scans: 5,
      available_projects: [],
      auto_scan_prompt: undefined,
    });
  });
});
