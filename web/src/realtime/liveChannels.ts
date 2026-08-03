/**
 * Registry of channel wrappers that own a socket but are constructed by their
 * consumer rather than by `channelRegistry`.
 *
 * `SessionIoChannel` and `AutoScanChannel` are built directly by WEB-05 and
 * WEB-10, so without this `stopAllRealtime()` could not reach them and a PTY or
 * auto-scan socket would outlive the session that authorised it — the relay
 * authenticates a mobile socket only at handshake, so it would stay fully
 * authorised for the previous account.
 *
 * Deliberately its own module with no realtime imports: `channels.ts` cannot
 * import `channelRegistry.ts` without closing an import cycle.
 */
export interface DisposableChannel {
  dispose: () => void;
}

const live = new Set<DisposableChannel>();

export function registerDisposable(channel: DisposableChannel): void {
  live.add(channel);
}

export function unregisterDisposable(channel: DisposableChannel): void {
  live.delete(channel);
}

/** Dispose every registered channel. Each `dispose()` deregisters itself. */
export function disposeAllChannels(): void {
  for (const channel of [...live]) channel.dispose();
  live.clear();
}
