import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { listAgents } from '../api/agentRepository';
import { cleanupTranscript, createVoiceApi } from '../api/voiceRepository';
import { queryKeys, wsBackedQueryOptions } from '../lib/queryKeys';
import type { VoiceCapability } from '../protocol/dto/agentVersion';
import { DictationController, type DictationState } from './dictationController';
import { VOICE_CODE } from './voiceApi';
import { WebAudioRecorder } from './recorder';

const IDLE: DictationState = { kind: 'idle' };

/** The review's text either side of its cursor (a selection is replaced). */
export interface CarriedText {
  before: string;
  after: string;
}

/**
 * Puts a transcript dictated from the review at its cursor, a space apart
 * from the words around it. `caret` is just past the inserted text.
 */
export function spliceTranscript(
  { before, after }: CarriedText,
  transcript: string,
): { text: string; caret: number } {
  const lead = before === '' || /\s$/.test(before) ? '' : ' ';
  const trail = after === '' || /^[\s.,;:!?)\]}]/.test(after) ? '' : ' ';
  const head = before + lead + transcript;
  return { text: head + trail + after, caret: head.length };
}

/**
 * One dictation controller per terminal (agent + session). A new pair of ids
 * — another session opened in the same route, an agent switch, leaving the
 * screen — disposes the old controller, which cancels its recording and
 * deletes the dictation on the agent, so audio and text never carry over.
 *
 * The actions call into the controller synchronously, which keeps the
 * click's user activation alive for the AudioContext.
 *
 * `cleanUp` sends each transcript through Claude Code on the agent before it
 * is shown; it is read when the transcript arrives.
 */
export function useDictation(
  agentId: string,
  sessionId: string,
  capability: VoiceCapability | null,
  cleanUp = false,
) {
  const [state, setState] = useState<DictationState>(IDLE);
  // The review's text around its cursor while the user dictates more: the
  // next result goes in between, and a cancel, an empty result or a failure
  // returns to reviewing it.
  const [carried, setCarried] = useState<CarriedText | null>(null);
  const queryClient = useQueryClient();
  const controllerRef = useRef<DictationController | null>(null);
  const sampleRate = capability?.sampleRate ?? null;
  const maxSeconds = capability?.maxSeconds ?? null;
  const cleanUpRef = useRef(cleanUp);
  useEffect(() => {
    cleanUpRef.current = cleanUp;
  }, [cleanUp]);

  useEffect(() => {
    if (sampleRate === null || maxSeconds === null) return;
    const controller = new DictationController({
      api: createVoiceApi(agentId, sessionId),
      createRecorder: () => new WebAudioRecorder(),
      sampleRate,
      maxSeconds,
      cleaner: () =>
        cleanUpRef.current ? (text, signal) => cleanupTranscript(agentId, text, signal) : null,
      onChange: (next) => {
        setState(next);
        // The agent says voice is not ready while the cached status says it
        // is: re-read it so the mic shows the real state.
        if (next.kind === 'failed' && next.code === VOICE_CODE.notReady) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.voiceStatus(agentId) });
        }
      },
    });
    controllerRef.current = controller;
    return () => {
      controller.dispose();
      if (controllerRef.current === controller) controllerRef.current = null;
      setState(IDLE);
      setCarried(null);
    };
  }, [agentId, sessionId, sampleRate, maxSeconds, queryClient]);

  const active =
    state.kind === 'starting' ||
    state.kind === 'recording' ||
    state.kind === 'transcribing' ||
    state.kind === 'cleaning';
  useKeepScreenAwake(active);

  const toggle = useCallback(() => {
    controllerRef.current?.toggle();
  }, []);
  const stop = useCallback(() => {
    void controllerRef.current?.stop();
  }, []);
  const cancel = useCallback(() => {
    controllerRef.current?.cancel();
  }, []);
  const retry = useCallback(() => {
    controllerRef.current?.retry();
  }, []);
  const skipCleanup = useCallback(() => {
    controllerRef.current?.skipCleanup();
  }, []);
  const dismiss = useCallback(() => {
    controllerRef.current?.dismiss();
  }, []);
  /** Records again from the review, keeping its text to insert the result into. */
  const continueFrom = useCallback((text: CarriedText) => {
    const controller = controllerRef.current;
    if (controller === null) return;
    setCarried(text);
    controller.toggle();
  }, []);
  /** Ends the review: the text was inserted or discarded. */
  const finish = useCallback(() => {
    setCarried(null);
    controllerRef.current?.dismiss();
  }, []);

  return {
    state,
    carried,
    toggle,
    stop,
    cancel,
    retry,
    skipCleanup,
    dismiss,
    continueFrom,
    finish,
  };
}

/**
 * Holds a screen wake lock while `active`, so a phone does not dim and lock
 * mid-dictation. The browser drops the lock when the page is hidden; it is
 * taken again when the page is visible and still `active`.
 */
function useKeepScreenAwake(active: boolean): void {
  useEffect(() => {
    if (!active || !('wakeLock' in navigator)) return;
    let sentinel: WakeLockSentinel | null = null;
    let released = false;
    const acquire = () => {
      if (document.visibilityState !== 'visible') return;
      navigator.wakeLock.request('screen').then(
        (lock) => {
          if (released) void lock.release();
          else sentinel = lock;
        },
        () => {},
      );
    };
    acquire();
    document.addEventListener('visibilitychange', acquire);
    return () => {
      released = true;
      document.removeEventListener('visibilitychange', acquire);
      void sentinel?.release();
    };
  }, [active]);
}

/** The agent's display name for copy like "Voice can't run on <agent>". */
export function useAgentName(agentId: string): string {
  const name = useQuery({
    queryKey: queryKeys.agents(),
    queryFn: listAgents,
    ...wsBackedQueryOptions,
    select: (agents) => agents.find((agent) => agent.agentId === agentId)?.name,
    meta: { silent: true },
  }).data;
  return name ?? 'your computer';
}
