import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { listAgents } from '../api/agentRepository';
import { createVoiceApi } from '../api/voiceRepository';
import { queryKeys, wsBackedQueryOptions } from '../lib/queryKeys';
import type { VoiceCapability } from '../protocol/dto/agentVersion';
import { DictationController, type DictationState } from './dictationController';
import { VOICE_CODE } from './voiceApi';
import { WebAudioRecorder } from './recorder';

const IDLE: DictationState = { kind: 'idle' };

/** Dictating more from the review appends to its text, one space apart. */
export function joinTranscript(before: string, after: string): string {
  if (before.trim() === '') return after;
  return /\s$/.test(before) ? before + after : `${before} ${after}`;
}

/**
 * One dictation controller per terminal (agent + session). A new pair of ids
 * — another session opened in the same route, an agent switch, leaving the
 * screen — disposes the old controller, which cancels its recording and
 * deletes the dictation on the agent, so audio and text never carry over.
 *
 * The actions call into the controller synchronously, which keeps the
 * click's user activation alive for the AudioContext.
 */
export function useDictation(
  agentId: string,
  sessionId: string,
  capability: VoiceCapability | null,
) {
  const [state, setState] = useState<DictationState>(IDLE);
  // Text kept from the review while the user dictates more: the next result
  // is appended to it, and a cancel, an empty result or a failure returns to
  // reviewing it.
  const [carried, setCarried] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const controllerRef = useRef<DictationController | null>(null);
  const sampleRate = capability?.sampleRate ?? null;
  const maxSeconds = capability?.maxSeconds ?? null;

  useEffect(() => {
    if (sampleRate === null || maxSeconds === null) return;
    const controller = new DictationController({
      api: createVoiceApi(agentId, sessionId),
      createRecorder: () => new WebAudioRecorder(),
      sampleRate,
      maxSeconds,
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
  const dismiss = useCallback(() => {
    controllerRef.current?.dismiss();
  }, []);
  /** Records again from the review, keeping `text` to append the result to. */
  const continueFrom = useCallback((text: string) => {
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

  return { state, carried, toggle, stop, cancel, retry, dismiss, continueFrom, finish };
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
