import { describe, expect, it } from 'vitest';
import { attentionDelivery, type AttentionContext } from './attentionSuppression';

function context(overrides: Partial<AttentionContext> = {}): AttentionContext {
  return {
    documentHidden: false,
    visibleSessionId: null,
    eventSessionId: 's-1',
    enabled: true,
    permission: 'granted',
    ...overrides,
  };
}

describe('attentionDelivery', () => {
  it('shows a system notification only while the tab is hidden', () => {
    expect(attentionDelivery(context({ documentHidden: true }))).toBe('system');
    expect(attentionDelivery(context({ documentHidden: false }))).toBe('toast');
  });

  it('says nothing about the session the user is already looking at', () => {
    // The rule both mobile clients share, in `shouldNotify` / `shouldSuppress`.
    expect(
      attentionDelivery(context({ visibleSessionId: 's-1', eventSessionId: 's-1' })),
    ).toBe('none');
  });

  it('still alerts about a different session while a terminal is open', () => {
    // Most of why the feature exists: one session waiting while you work in
    // another is exactly the case worth interrupting for.
    expect(
      attentionDelivery(context({ visibleSessionId: 's-2', eventSessionId: 's-1' })),
    ).toBe('toast');
  });

  it('alerts about the visible session once the tab is hidden', () => {
    // "Looking at it" stops being true the moment the tab is backgrounded.
    expect(
      attentionDelivery(
        context({ documentHidden: true, visibleSessionId: 's-1', eventSessionId: 's-1' }),
      ),
    ).toBe('system');
  });

  it('drops a hidden-tab alert without permission rather than queuing a toast', () => {
    // A toast has nowhere to appear in a background tab, and its 5 s
    // auto-dismiss would expire long before anyone looked.
    for (const permission of ['denied', 'default', 'unsupported'] as const) {
      expect(attentionDelivery(context({ documentHidden: true, permission }))).toBe('none');
    }
  });

  it('shows an in-app toast in a visible tab regardless of permission', () => {
    expect(attentionDelivery(context({ permission: 'denied' }))).toBe('toast');
    expect(attentionDelivery(context({ permission: 'unsupported' }))).toBe('toast');
  });

  it('silences everything when the user opted out', () => {
    expect(attentionDelivery(context({ enabled: false }))).toBe('none');
    expect(attentionDelivery(context({ enabled: false, documentHidden: true }))).toBe('none');
  });
});
