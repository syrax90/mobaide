import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { TerminalViewState } from '../../terminal/sessionModel';
import { ConnectionOverlay, overlayMessage } from './ConnectionOverlay';

type OverlayView = Pick<
  TerminalViewState,
  'channelStopped' | 'connection' | 'replayPainting' | 'suspended'
>;

const view = (patch: Partial<OverlayView> = {}): OverlayView => ({
  channelStopped: false,
  connection: 'live',
  replayPainting: false,
  suspended: false,
  ...patch,
});

describe('overlayMessage', () => {
  it('calls a channel that gave up disconnected, not a session that ended', () => {
    // Nothing in the close policy reports a session's death this way. The two
    // paths that stop a session channel are a terminal close code and five
    // failed agent-token mints during an outage — the session on the agent is
    // very likely still alive, and the user is one button away from it.
    expect(overlayMessage(view({ channelStopped: true }))).toBe('Disconnected');
  });

  it('outranks the reconnect label, which would promise a retry nobody armed', () => {
    expect(
      overlayMessage(view({ channelStopped: true, connection: 'connecting' })),
    ).toBe('Disconnected');
  });

  it('calls a deliberate detach paused, matching Android and iOS', () => {
    // A suspend leaves `connection` at `'connecting'`, so without its own
    // branch the overlay would tell the user their connection had dropped.
    expect(overlayMessage(view({ suspended: true, connection: 'connecting' }))).toBe(
      'Paused',
    );
  });

  it('keeps the pill up until the replayed buffer has been painted', () => {
    expect(overlayMessage(view({ connection: 'connecting' }))).toBe('Reconnecting…');
    expect(overlayMessage(view({ connection: 'replaying' }))).toBe('Replaying buffer…');
    // `'e'` flips the connection to live while the parser is still consuming
    // history, so the overlay must not lift on the marker alone.
    expect(overlayMessage(view({ replayPainting: true }))).toBe('Replaying buffer…');
  });

  it('shows nothing on a live, painted screen', () => {
    expect(overlayMessage(view())).toBeNull();
  });
});

describe('ConnectionOverlay', () => {
  afterEach(cleanup);

  it('carries no button while the channel is still retrying on its own', () => {
    render(<ConnectionOverlay message="Reconnecting…" />);

    expect(screen.getByText('Reconnecting…')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reconnect' })).not.toBeInTheDocument();
  });

  it('re-arms a stopped channel from the button it carries', () => {
    let retries = 0;
    render(
      <ConnectionOverlay
        message="Disconnected"
        onRetry={() => {
          retries += 1;
        }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));

    expect(retries).toBe(1);
  });
});
