/**
 * The live chat models, keyed by `chat:<agent>:<session>` — the chat
 * counterpart of `terminalStore`, bounded the same two ways: a session the
 * agent reports gone is evicted (unless it is on screen, where it stays
 * readable and goes when the screen leaves), and only `MAX_RETAINED` stay
 * resident.
 */
import { create } from 'zustand';
import { chatChannelName } from '../realtime/channels';
import { setChatEvictor } from '../terminal/terminalEviction';
import { mountedTerminal } from '../terminal/watchedTerminal';
import { ChatSessionModel } from './chatModel';

const MAX_RETAINED = 4;

interface ChatStoreState {
  models: Record<string, ChatSessionModel>;
  /** Keys most-recently-acquired last. */
  order: string[];
}

export const useChatStore = create<ChatStoreState>()(() => ({ models: {}, order: [] }));

function touch(key: string, order: readonly string[]): string[] {
  return [...order.filter((entry) => entry !== key), key];
}

function forgetChat(key: string): void {
  const { models, order } = useChatStore.getState();
  const { [key]: removed, ...rest } = models;
  if (removed === undefined) return;
  useChatStore.setState({ models: rest, order: order.filter((entry) => entry !== key) });
}

/** Idempotent per key: StrictMode runs the acquiring effect twice. */
export function acquireChat(agentId: string, sessionId: string): ChatSessionModel {
  const key = chatChannelName(agentId, sessionId);
  const { models, order } = useChatStore.getState();
  const existing = models[key];
  if (existing !== undefined) {
    useChatStore.setState({ order: touch(key, order) });
    return existing;
  }
  const model = new ChatSessionModel(agentId, sessionId, {
    onDispose: () => {
      forgetChat(key);
    },
  });
  const nextOrder = touch(key, order);
  const evicted = nextOrder.slice(0, Math.max(0, nextOrder.length - MAX_RETAINED));
  const nextModels: Record<string, ChatSessionModel> = { ...models, [key]: model };
  for (const staleKey of evicted) delete nextModels[staleKey];
  useChatStore.setState({ models: nextModels, order: nextOrder.slice(evicted.length) });
  for (const staleKey of evicted) models[staleKey]?.dispose();
  return model;
}

/** The screen left this session: drop the model if the session is over. */
export function releaseChat(key: string): void {
  const model = useChatStore.getState().models[key];
  if (model?.isGone() === true) model.dispose();
}

export function evictChatSessions(agentId: string, sessionIds: readonly string[]): void {
  const { models } = useChatStore.getState();
  const onScreen = mountedTerminal();
  for (const sessionId of sessionIds) {
    const key = chatChannelName(agentId, sessionId);
    const model = models[key];
    if (model === undefined) continue;
    if (key === onScreen) model.markEvicted();
    else model.dispose();
  }
}

setChatEvictor(evictChatSessions);
