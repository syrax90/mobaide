import { describe, expect, it } from 'vitest';
import { gitJobSchema } from './gitJob';

const wire = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  job_id: 'j1',
  session_id: 's1',
  op: 'push',
  status: 'running',
  ...over,
});

describe('gitJobSchema', () => {
  it('reads an absent or null ok as undefined and keeps an explicit false', () => {
    expect(gitJobSchema.parse(wire()).ok).toBeUndefined();
    expect(gitJobSchema.parse(wire({ ok: null })).ok).toBeUndefined();
    expect(gitJobSchema.parse(wire({ ok: false })).ok).toBe(false);
    expect(gitJobSchema.safeParse(wire({ ok: 'no' })).success).toBe(false);
  });
});
