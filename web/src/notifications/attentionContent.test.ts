import { describe, expect, it } from 'vitest';
import { attentionContent, attentionLabel, FALLBACK_LABEL } from './attentionContent';

describe('attentionContent', () => {
  // Literal expectations on purpose. These five pairs are the cross-client
  // contract an iPhone receives over APNs while this tab shows its own copy of
  // the same alert; deriving them from a helper here would let both sides drift
  // together and still pass.
  it('renders the permission alert', () => {
    expect(attentionContent({ reason: 'awaiting_permission', title: 'api refactor' })).toEqual({
      title: 'Permission needed',
      body: 'api refactor is waiting for your approval',
    });
  });

  it('renders the turn-finished alert', () => {
    expect(attentionContent({ reason: 'turn_finished' })).toEqual({
      title: 'Your move',
      body: 'Claude session finished its turn — tap to continue',
    });
  });

  it('renders the turn-failed alert', () => {
    expect(attentionContent({ reason: 'turn_failed', title: 'api refactor' })).toEqual({
      title: 'Turn failed',
      body: 'api refactor hit an error — tap to see what happened',
    });
  });

  it('renders the ended alert with and without an exit reason', () => {
    expect(attentionContent({ reason: 'ended', title: 'api refactor', exit_reason: 'clear' })).toEqual(
      { title: 'Session ended (clear)', body: 'api refactor' },
    );
    expect(attentionContent({ reason: 'ended', title: 'api refactor' })).toEqual({
      title: 'Session ended',
      body: 'api refactor',
    });
    expect(
      attentionContent({ reason: 'ended', title: 'api refactor', exit_reason: '   ' }),
    ).toEqual({ title: 'Session ended', body: 'api refactor' });
  });

  it('posts nothing for an absent or unrecognised reason', () => {
    // A newer agent reason must not surface as an alert with an empty body.
    expect(attentionContent({})).toBeNull();
    expect(attentionContent({ reason: 'awaiting_coffee' })).toBeNull();
    expect(attentionContent({ reason: '' })).toBeNull();
  });
});

describe('attentionLabel', () => {
  it('prefers the title, then the project, then the fallback', () => {
    expect(attentionLabel({ title: 'a', project_name: 'b' })).toBe('a');
    expect(attentionLabel({ project_name: 'b' })).toBe('b');
    expect(attentionLabel({})).toBe(FALLBACK_LABEL);
  });

  it('treats a blank field as absent, matching every other client', () => {
    expect(attentionLabel({ title: '   ', project_name: 'b' })).toBe('b');
    expect(attentionLabel({ title: '', project_name: '  ' })).toBe(FALLBACK_LABEL);
  });

  it.each([
    // An agent too old to send `kind` only alerted for Claude sessions.
    [{}, 'Claude session'],
    [{ kind: '' }, 'Claude session'],
    [{ kind: 'claude' }, 'Claude session'],
    [{ kind: 'shell' }, 'Terminal session'],
    [{ kind: 'custom_0123456789ab', kind_label: 'My Linter' }, 'My Linter session'],
    [{ kind: 'custom_0123456789ab', kind_label: '  ' }, 'Agent session'],
    [{ kind: 'custom_0123456789ab' }, 'Agent session'],
    // A kind named like an Object.prototype member is still just unknown.
    [{ kind: 'toString' }, 'Agent session'],
  ])('names an untitled session after its kind: %j', (source, expected) => {
    expect(attentionLabel({ ...source, title: ' ', project_name: '' })).toBe(expected);
  });

  it('still prefers the title and the project over the kind', () => {
    expect(attentionLabel({ title: 'a', project_name: 'b', kind: 'codex' })).toBe('a');
    expect(attentionLabel({ project_name: 'b', kind: 'codex' })).toBe('b');
  });

  it('does not trim a label it accepts', () => {
    // Blankness decides *whether* to use the field; the value itself goes
    // through verbatim, as it does on iOS and the server.
    expect(attentionLabel({ title: ' spaced ' })).toBe(' spaced ');
  });
});
