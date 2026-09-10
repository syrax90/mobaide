import type { PointerEvent } from 'react';

/**
 * A pointerdown a control acts on: the primary pointer's primary button.
 *
 * Without it a right-click would open the context menu *and* start a drag or
 * arm a click latch, a middle-click would page, and a second finger landing
 * during a drag would fight the first.
 */
export function isPrimaryPress(event: PointerEvent<HTMLElement>): boolean {
  return event.isPrimary && event.button === 0;
}
