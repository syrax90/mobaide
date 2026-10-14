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

  return { state, toggle, stop, cancel, retry, dismiss };
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
