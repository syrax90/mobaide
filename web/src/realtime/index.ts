/**
 * Public surface of the realtime layer — re-exports only.
 *
 * The channel lifetimes live in `./channelRegistry`; the wire layer they speak
 * is `src/protocol/`.
 */
export {
  connectAgentEvents,
  disconnectAgentEvents,
  startUserEvents,
  stopAllRealtime,
  stopUserEvents,
} from './channelRegistry';
export { AutoScanChannel, SessionIoChannel } from './channels';
export { useConnectionStore } from './connectionStore';
export { RealtimeProvider } from './RealtimeProvider';
export type { ChannelEvent, ChannelState, ChannelStatus } from './webSocketChannel';
