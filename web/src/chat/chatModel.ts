/**
 * One attached chat session: the socket, the timeline and the commands the
 * screen sends. It lives outside React (in `chatStore`) so going back to a
 * session renders at once from the cached timeline and syncs only what was
 * missed.
 *
 * A session nobody is looking at (hidden tab, or another screen) drops its
 * socket after a grace period: unlike the PTY, a chat resumes from `seq`, so
 * coming back costs only the events that happened meanwhile.
 */
import { create, type StoreApi, type UseBoundStore } from 'zustand';
import { ChatSessionChannel, chatChannelName } from '../realtime/channels';
import { useConnectionStore } from '../realtime/connectionStore';
import { registerDisposable, unregisterDisposable } from '../realtime/liveChannels';
import { onResume } from '../realtime/resumeOnFocus';
import { onTabVisibility, tabHidden } from '../realtime/tabVisibility';
import { realScheduler, type ChannelSend, type Scheduler } from '../realtime/webSocketChannel';
import { mountedTerminal, onMountedTerminal } from '../terminal/watchedTerminal';
import { MAX_CHAT_IMAGES, MAX_TOTAL_BASE64, totalBase64, type PreparedImage } from './chatImages';
import {
  INITIAL_CHAT_TIMELINE,
  applyEvents,
  applySnapshot,
  chatOpened,
  resumeOf,
  type ChatReduction,
  type ChatTimeline,
} from './chatTimeline';
import {
  decodeChatInbound,
  encodeChatCommand,
  formResponseCommand,
  promptCommand,
  syncCommand,
  type ChatCommand,
  type ChatFrame,
  type ChatImage,
  type FormValue,
} from './chatWire';

const UNWATCHED_GRACE_MS = 60_000;
/** The first silent window; each next one doubles: 10 s, 20 s, 40 s. */
const SYNC_TIMEOUT_MS = 10_000;
const SILENT_SYNC_TIMEOUTS = 3;
const OVERRIDE_SETTLE_MS = 3_000;
/** Every command is answered at once or within the agent's own 20 s config timeout. */
const COMMAND_TIMEOUT_MS = 30_000;

/**
 * The deadline starts when the frame is queued, and a prompt can carry 3.5 MB
 * of images: allow 20 s per MB to upload it on a slow link.
 */
function commandTimeoutMs(frameBytes: number): number {
  return COMMAND_TIMEOUT_MS + Math.floor(frameBytes / 100_000) * 2_000;
}

export type ChatConnection = 'connecting' | 'live';

export interface ChatViewState {
  connection: ChatConnection;
  /** The reconnect loop gave up; only a user retry re-arms it. */
  channelStopped: boolean;
  suspended: boolean;
  /** The session is over: `'s'` frame `stopped`, or it left the agent's list. */
  gone: boolean;
  /** A `restart` is waiting for its result. */
  restarting: boolean;
  timeline: ChatTimeline;
  /** Config values set here and not yet confirmed by the agent's state. */
  overrides: Readonly<Record<string, string | boolean>>;
  /** The images attached to the draft; kept here so a remounted composer sees late ones. */
  draftImages: readonly PreparedImage[];
  /** Images still being prepared for the draft. */
  preparingImages: number;
}

export type ChatResult =
  | { ok: true; data: unknown }
  | {
      ok: false;
      error: string;
      /** The socket dropped before an answer: the command may or may not have run. */
      lost: boolean;
    };

const NOT_CONNECTED: ChatResult = { ok: false, error: 'Not connected to the session.', lost: false };
const CONNECTION_LOST: ChatResult = { ok: false, error: 'The connection dropped.', lost: true };

function initialView(): ChatViewState {
  return {
    connection: 'connecting',
    channelStopped: false,
    suspended: false,
    gone: false,
    restarting: false,
    timeline: INITIAL_CHAT_TIMELINE,
    overrides: {},
    draftImages: [],
    preparingImages: 0,
  };
}

function randomClientId(): string {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export interface ChatSessionModelOptions {
  onDispose?: () => void;
  scheduler?: Scheduler;
}

export class ChatSessionModel {
  readonly key: string;
  readonly agentId: string;
  readonly sessionId: string;
  readonly store: UseBoundStore<StoreApi<ChatViewState>>;
  /** The composer's unsent text, kept across remounts of the screen. */
  draft = '';

  private readonly channel: ChatSessionChannel;
  private readonly scheduler: Scheduler;
  private readonly onDispose: (() => void) | undefined;
  private readonly clientId = randomClientId();
  private readonly disposers: (() => void)[] = [];
  private readonly pending = new Map<string, (result: ChatResult) => void>();
  /** Override ids the agent accepted; dropped on the next config change it reports. */
  private readonly settledOverrides = new Set<string>();
  private counter = 0;
  private disposed = false;
  private suspended = false;
  private evicted = false;
  private cancelSuspend: (() => void) | null = null;
  private cancelSyncTimeout: (() => void) | null = null;
  private silentSyncTimeouts = 0;
  private snapshotRequested = false;
  private cancelSettle: (() => void) | null = null;
  private restartInFlight: Promise<ChatResult> | null = null;

  constructor(agentId: string, sessionId: string, options: ChatSessionModelOptions = {}) {
    this.agentId = agentId;
    this.sessionId = sessionId;
    this.key = chatChannelName(agentId, sessionId);
    this.scheduler = options.scheduler ?? realScheduler;
    this.onDispose = options.onDispose;
    this.store = create<ChatViewState>()(() => initialView());
    this.channel = new ChatSessionChannel(agentId, sessionId, (send) => {
      this.handleOpen(send);
    });

    this.disposers.push(
      this.channel.channel.subscribe((event) => {
        switch (event.kind) {
          case 'connected':
            this.patch({ connection: 'live' });
            break;
          case 'binary':
            this.handleInbound(new Uint8Array(event.data));
            break;
          case 'disconnected':
            this.noteDisconnected();
            break;
          default:
            break;
        }
      }),
    );
    this.disposers.push(
      useConnectionStore.subscribe(() => {
        this.publishChannelStopped();
      }),
    );
    this.disposers.push(
      onTabVisibility(() => {
        this.syncWatched();
      }),
    );
    this.disposers.push(
      onMountedTerminal(() => {
        this.syncWatched();
      }),
    );
    // A socket can die without a close event (sleep, network switch) while
    // the tab stays visible: drop it and its unanswered commands.
    this.disposers.push(
      onResume(() => {
        if (this.suspended || this.disposed) return;
        this.reconnectNow();
      }),
    );

    registerDisposable(this);
    this.channel.channel.connect();
    this.syncWatched();
  }

  state(): ChatViewState {
    return this.store.getState();
  }

  /** The images attached to the draft; the model revokes their previews when disposed. */
  get draftImages(): readonly PreparedImage[] {
    return this.store.getState().draftImages;
  }

  set draftImages(next: readonly PreparedImage[]) {
    this.patch({ draftImages: next });
  }

  /** Count images that start (`delta` > 0) or finish (`delta` < 0) preparing. */
  notePreparingImages(delta: number): void {
    this.patch({ preparingImages: Math.max(0, this.store.getState().preparingImages + delta) });
  }

  /**
   * Attach an image whose preparation finished, even if the composer that
   * started it is gone. False when it would take the draft past the size of
   * one prompt; its preview is then revoked, as it is once the model is disposed.
   */
  attachDraftImage(image: PreparedImage): boolean {
    const current = this.draftImages;
    if (totalBase64(current) + image.data.length > MAX_TOTAL_BASE64) {
      URL.revokeObjectURL(image.previewUrl);
      return false;
    }
    if (this.disposed) {
      URL.revokeObjectURL(image.previewUrl);
      return true;
    }
    this.draftImages = [...current, image];
    return true;
  }

  /** Whether the session is known to be over, so the model can go once off screen. */
  isGone(): boolean {
    return this.evicted || this.store.getState().gone;
  }

  /** The session left the agent's list while this model was on screen. */
  markEvicted(): void {
    this.evicted = true;
    this.patch({ gone: true });
  }

  prompt(text: string, images: readonly ChatImage[]): Promise<ChatResult> {
    return this.command(promptCommand(text, images));
  }

  cancel(): Promise<ChatResult> {
    return this.command({ op: 'cancel' });
  }

  /** A second call while one is in flight shares its result instead of sending again. */
  restart(): Promise<ChatResult> {
    if (this.restartInFlight !== null) return this.restartInFlight;
    this.patch({ restarting: true });
    const done = this.command({ op: 'restart' }).then((result) => {
      this.restartInFlight = null;
      this.patch({ restarting: false });
      return result;
    });
    this.restartInFlight = done;
    return done;
  }

  dequeue(queueId: string): Promise<ChatResult> {
    return this.command({ op: 'dequeue', queue_id: queueId });
  }

  /** Send a queued message into the running turn now. */
  steer(queueId: string): Promise<ChatResult> {
    return this.command({ op: 'steer', queue_id: queueId });
  }

  answerPermission(requestId: string, optionId: string): Promise<ChatResult> {
    return this.command({ op: 'respond', request_id: requestId, option_id: optionId });
  }

  answerForm(
    requestId: string,
    action: 'accept' | 'decline' | 'cancel',
    values?: Record<string, FormValue>,
  ): Promise<ChatResult> {
    return this.command(formResponseCommand(requestId, action, values));
  }

  /** Shows the new value at once; the agent's state, or a refusal, settles it. */
  async setConfig(configId: string, value: string | boolean): Promise<ChatResult> {
    this.settledOverrides.delete(configId);
    this.patch({ overrides: { ...this.state().overrides, [configId]: value } });
    const result = await this.command({ op: 'set_config', config_id: configId, value });
    if (this.state().overrides[configId] !== value) return result;
    if (!result.ok || this.configValue(configId) === value) {
      this.dropOverrides([configId]);
    } else {
      this.settledOverrides.add(configId);
      this.cancelSettle?.();
      this.cancelSettle = this.scheduler.schedule(() => {
        this.cancelSettle = null;
        this.dropOverrides([...this.settledOverrides]);
      }, OVERRIDE_SETTLE_MS);
    }
    return result;
  }

  /** Re-arm a channel that gave up. */
  retryConnection(): void {
    if (this.disposed) return;
    this.resume();
    this.channel.channel.connect();
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    unregisterDisposable(this);
    this.cancelSuspend?.();
    this.cancelSyncTimeout?.();
    this.cancelSettle?.();
    for (const dispose of this.disposers.splice(0)) dispose();
    this.resolvePending(CONNECTION_LOST);
    for (const image of this.draftImages) URL.revokeObjectURL(image.previewUrl);
    this.draftImages = [];
    this.channel.dispose();
    this.onDispose?.();
  }

  /**
   * Put a prompt the agent did not take back into the draft, whether or not
   * the composer is on screen: its text goes before what the user typed
   * since, and its images before theirs, within the limits of one prompt.
   */
  restoreDraft(text: string, images: readonly PreparedImage[]): void {
    if (this.draft.trim() === '') this.draft = text;
    else if (text !== '') this.draft = `${text}\n${this.draft}`;
    let total = 0;
    const kept: PreparedImage[] = [];
    for (const image of [...images, ...this.draftImages]) {
      const fits =
        kept.length < MAX_CHAT_IMAGES && total + image.data.length <= MAX_TOTAL_BASE64;
      if (this.disposed || !fits) {
        URL.revokeObjectURL(image.previewUrl);
      } else {
        kept.push(image);
        total += image.data.length;
      }
    }
    this.draftImages = kept;
  }

  private patch(next: Partial<ChatViewState>): void {
    const current = this.store.getState();
    for (const [key, value] of Object.entries(next)) {
      if (current[key as keyof ChatViewState] !== value) {
        this.store.setState(next);
        return;
      }
    }
  }

  private nextId(): string {
    this.counter += 1;
    return `${this.clientId}-${String(this.counter)}`;
  }

  private command(command: ChatCommand): Promise<ChatResult> {
    if (this.disposed) return Promise.resolve(NOT_CONNECTED);
    const id = this.nextId();
    const frame = encodeChatCommand(id, command);
    if (!this.channel.send(frame)) return Promise.resolve(NOT_CONNECTED);
    return new Promise((resolve) => {
      // No answer in time: the socket is most likely half-open.
      const cancelTimeout = this.scheduler.schedule(() => {
        if (!this.pending.delete(id)) return;
        resolve(CONNECTION_LOST);
        this.reconnectNow();
      }, commandTimeoutMs(frame.length));
      this.pending.set(id, (result) => {
        cancelTimeout();
        resolve(result);
      });
    });
  }

  /** `forceReconnect()` emits no `disconnected`, so the pending commands are settled here. */
  private reconnectNow(): void {
    this.noteDisconnected();
    this.channel.channel.forceReconnect();
  }

  private resolvePending(result: ChatResult): void {
    const waiting = [...this.pending.values()];
    this.pending.clear();
    for (const resolve of waiting) resolve(result);
  }

  private handleOpen(send: ChannelSend): void {
    this.applyReduction(chatOpened(this.state().timeline), send);
  }

  private applyReduction(reduction: ChatReduction, send?: ChannelSend): void {
    const previous = this.state().timeline;
    this.patch({ timeline: reduction.timeline });
    if (reduction.timeline.state.config !== previous.state.config) this.onConfigChanged();
    if (reduction.sync !== undefined) {
      const bytes = encodeChatCommand(this.nextId(), syncCommand(reduction.sync));
      if (send !== undefined) send(bytes);
      else this.channel.send(bytes);
      this.snapshotRequested = reduction.sync === null;
      this.armSyncTimeout();
    } else if (!reduction.timeline.syncPending) {
      this.cancelSyncTimeout?.();
      this.cancelSyncTimeout = null;
    }
  }

  private armSyncTimeout(): void {
    this.cancelSyncTimeout?.();
    this.cancelSyncTimeout = this.scheduler.schedule(() => {
      this.cancelSyncTimeout = null;
      const timeline = this.state().timeline;
      if (!timeline.syncPending) return;
      // Misses in a row with no frame at all: the socket died after it opened.
      // Not sooner — a big snapshot on a slow link can outlast the first ones.
      this.silentSyncTimeouts += 1;
      if (this.silentSyncTimeouts >= SILENT_SYNC_TIMEOUTS) {
        this.reconnectNow();
        return;
      }
      // A snapshot arrives as one whole message: asking again only queues a second.
      if (this.snapshotRequested) {
        this.armSyncTimeout();
        return;
      }
      // The agent answers with a snapshot itself when it cannot resume.
      this.applyReduction({ timeline, sync: resumeOf(timeline) });
    }, SYNC_TIMEOUT_MS * 2 ** this.silentSyncTimeouts);
  }

  private handleInbound(bytes: Uint8Array): void {
    this.silentSyncTimeouts = 0;
    const inbound = decodeChatInbound(bytes);
    if (inbound === null) return;
    if (inbound.kind === 'state') {
      if (inbound.status === 'stopped') this.patch({ gone: true });
      return;
    }
    this.handleFrame(inbound.frame);
  }

  private handleFrame(frame: ChatFrame): void {
    switch (frame.t) {
      case 'result': {
        const resolve = this.pending.get(frame.id);
        if (resolve === undefined) return;
        this.pending.delete(frame.id);
        resolve(
          frame.ok
            ? { ok: true, data: frame.data }
            : { ok: false, error: frame.error ?? 'The agent refused the request.', lost: false },
        );
        return;
      }
      case 'snapshot':
        this.applyReduction(applySnapshot(this.state().timeline, frame));
        return;
      case 'events':
        this.applyReduction(applyEvents(this.state().timeline, frame));
        return;
    }
  }

  private configValue(configId: string): string | boolean | undefined {
    return this.state().timeline.state.config.find((option) => option.id === configId)?.value;
  }

  private onConfigChanged(): void {
    const overrides = this.state().overrides;
    const done = Object.keys(overrides).filter(
      (id) => this.settledOverrides.has(id) || this.configValue(id) === overrides[id],
    );
    if (done.length > 0) this.dropOverrides(done);
  }

  private dropOverrides(ids: readonly string[]): void {
    if (ids.length === 0) return;
    const next = { ...this.state().overrides };
    for (const id of ids) {
      delete next[id];
      this.settledOverrides.delete(id);
    }
    this.patch({ overrides: next });
  }

  private noteDisconnected(): void {
    this.cancelSyncTimeout?.();
    this.cancelSyncTimeout = null;
    this.silentSyncTimeouts = 0;
    this.resolvePending(CONNECTION_LOST);
    this.settledOverrides.clear();
    this.patch({ connection: 'connecting', overrides: {} });
  }

  private publishChannelStopped(): void {
    this.patch({
      channelStopped:
        !this.suspended &&
        useConnectionStore.getState().channels[this.key]?.status === 'stopped',
    });
  }

  private syncWatched(): void {
    if (this.disposed) return;
    if (!tabHidden() && mountedTerminal() === this.key) {
      this.cancelSuspend?.();
      this.cancelSuspend = null;
      this.resume();
      return;
    }
    if (this.suspended || this.cancelSuspend !== null) return;
    this.cancelSuspend = this.scheduler.schedule(() => {
      this.cancelSuspend = null;
      this.suspend();
    }, UNWATCHED_GRACE_MS);
  }

  private suspend(): void {
    if (this.suspended || this.disposed) return;
    this.suspended = true;
    this.patch({ suspended: true });
    // `disconnect()` detaches the socket's handlers first, so no
    // `disconnected` event follows on its own.
    this.channel.channel.disconnect();
    this.noteDisconnected();
    this.publishChannelStopped();
  }

  private resume(): void {
    if (!this.suspended || this.disposed) return;
    this.suspended = false;
    this.patch({ suspended: false });
    this.channel.channel.connect();
    this.publishChannelStopped();
  }
}
