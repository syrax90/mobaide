import type { PointerEvent } from 'react';
import { describe, expect, it } from 'vitest';
import { isPrimaryPress } from './pointer';

/** Only the two fields the gate reads; nothing else on a pointer event matters to it. */
const press = (init: { button: number; isPrimary: boolean }): PointerEvent<HTMLElement> =>
  init as PointerEvent<HTMLElement>;

describe('isPrimaryPress', () => {
  it('accepts the primary pointer pressing its primary button', () => {
    expect(isPrimaryPress(press({ button: 0, isPrimary: true }))).toBe(true);
  });

  it('rejects any other button, which would otherwise arm a latch under a context menu', () => {
    expect(isPrimaryPress(press({ button: 2, isPrimary: true }))).toBe(false);
    expect(isPrimaryPress(press({ button: 1, isPrimary: true }))).toBe(false);
  });

  it('rejects a second pointer landing during the first', () => {
    expect(isPrimaryPress(press({ button: 0, isPrimary: false }))).toBe(false);
  });
});
