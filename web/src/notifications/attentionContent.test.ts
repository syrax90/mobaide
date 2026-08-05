import { describe, expect, it } from 'vitest';
import { attentionContent, attentionLabel, FALLBACK_LABEL } from './attentionContent';

describe('attentionContent', () => {
  // Literal expectations on purpose. These four pairs are the cross-client
  // contract an iPhone receives over APNs while this tab shows its own copy of
  // the same alert; deriving them from a helper here would let both sides drift
  // together and still pass.
  it('renders the permission alert', () => {
    expect(attentionContent({ reason: 'awaiting_permission', title: 'api refactor' })).toEqual({
      title: 'Permission needed',
      body: 'api refactor is waiting for your approval',
    });
  });

  it('renders the macOS permission alert', () => {
    expect(
      attentionContent({ reason: 'awaiting_os_permission', project_name: 'mobaide' }),
    ).toEqual({
      title: 'Action needed on your Mac',
      body: 'mobaide is paused — your Mac is asking to allow file access. Approve it on the Mac to continue.',
    });
  });

  it('renders the turn-finished alert', () => {
    expect(attentionContent({ reason: 'turn_finished' })).toEqual({
      title: 'Your move',
      body: 'Claude session finished its turn — tap to continue',
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

  it('uses em dashes, not hyphens', () => {
    // The three other implementations carry U+2014 with spaces around it. A
    // hyphen here would be invisible in review and audible in a diff of nothing.
    const os = attentionContent({ reason: 'awaiting_os_permission' });
    const turn = attentionContent({ reason: 'turn_finished' });
    expect(os?.body).toContain('—');
    expect(turn?.body).toContain('—');
    expect(os?.body).not.toContain(' - ');
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

  it('does not trim a label it accepts', () => {
    // Blankness decides *whether* to use the field; the value itself goes
    // through verbatim, as it does on iOS and the server.
    expect(attentionLabel({ title: ' spaced ' })).toBe(' spaced ');
  });
});
