import { useCallback, useLayoutEffect, useState } from 'react';
import { CLOUD_VOICE_STATE, VOICE_ENGINE } from '../protocol/dto/cloudVoice';
import { CLOUD_VOICE_NAME } from './cloudVoiceCopy';
import type { DictationState } from './dictationController';
import { useCloudVoice } from './useCloudVoice';
import { spliceTranscript, useAgentName, useDictation, type DictationSource } from './useDictation';
import {
  useVoiceCapability,
  useVoiceCleanupKinds,
  useVoiceCleanupSetting,
  useVoiceStatus,
  voiceQueryFailed,
} from './useVoiceStatus';
import { composerMicMode, voiceRoute } from './voiceEngine';
import { useMicHidden, useSkipReview, useVoicePrefsStore } from './voicePrefsStore';
import { voiceMicMode } from './voiceStatusView';

interface ComposerVoiceOptions {
  /**
   * Puts a transcript where the user types; false when the screen refused it.
   * Keep it stable (`useCallback`): a result is decided against it.
   */
  insert: (text: string) => boolean;
  /** The session's kind, which decides whether transcript cleanup is offered; undefined until known. */
  sessionKind: string | undefined;
}

/**
 * Voice input of a session screen: the mic's state, the dictation and its
 * review. `ComposerVoiceDialogs` renders the dialogs from the result.
 */
export function useComposerVoice(
  agentId: string,
  sessionId: string,
  { insert, sessionKind }: ComposerVoiceOptions,
) {
  // On-computer voice: only on an agent that advertises `capabilities.voice`,
  // styled by its live status (`voice.changed` arrives on the agent channel
  // `useSessionGone` already holds open).
  const capabilityQuery = useVoiceCapability(agentId);
  const capability = capabilityQuery.data ?? null;
  const status = useVoiceStatus(agentId, capability !== null);
  // Whether the agent's own voice is known yet: until then "no choice" could
  // still turn out to be an agent that already has voice enabled.
  const probeFailed = voiceQueryFailed(capabilityQuery);
  const statusFailed = voiceQueryFailed(status);
  const localKnown =
    (probeFailed && capability === null) ||
    (!capabilityQuery.isPending && (capability === null || status.data !== undefined || statusFailed));
  const micHidden = useMicHidden(agentId);
  // Cloud voice input needs nothing from the agent, so the mic also shows on
  // an agent without voice of its own.
  const cloudQuery = useCloudVoice();
  const cloud = cloudQuery.data;
  const route = voiceRoute(cloud, agentId, status.data);
  const micMode = composerMicMode(
    route,
    cloud,
    voiceMicMode(capability, status.data, micHidden),
    micHidden || (route === 'choose' && !localKnown),
  );
  // Transcript cleanup runs the session's AI agent on the agent's computer,
  // offered for the session kinds the agent lists; there the CLI is known to
  // be there. The speech service already returns clean text, so cloud
  // transcripts skip it.
  const cleanupKinds = useVoiceCleanupKinds(agentId).data;
  const cleanupKind =
    route === 'local' && sessionKind !== undefined && cleanupKinds?.includes(sessionKind) === true
      ? sessionKind
      : null;
  const cleanUp = useVoiceCleanupSetting(agentId, status.data);
  // A source only while something can record: any other state disposes the
  // controller, which cancels a recording nobody can take any more.
  let source: DictationSource | null = null;
  if (micMode === 'ready') {
    if (route === 'cloud' && cloud?.state === CLOUD_VOICE_STATE.ready) {
      source = {
        engine: VOICE_ENGINE.cloud,
        sampleRate: cloud.limits.sample_rate,
        maxSeconds: cloud.limits.max_seconds,
      };
    } else if (route === 'local' && capability !== null) {
      source = {
        engine: VOICE_ENGINE.local,
        sampleRate: capability.sampleRate,
        maxSeconds: capability.maxSeconds,
      };
    }
  }
  const dictation = useDictation(
    agentId,
    sessionId,
    source,
    cleanupKind === null ? null : { kind: cleanupKind, on: cleanUp.on },
  );
  const [setupOpen, setSetupOpen] = useState(false);
  const agentName = useAgentName(agentId);

  const { carried, finish } = dictation;
  const insertTranscript = useCallback(
    (text: string): boolean => {
      if (!insert(text)) return false;
      finish();
      return true;
    },
    [insert, finish],
  );
  // With the review turned off a result goes straight to the prompt; only an
  // insert the screen refuses falls back to showing it for review. Decided
  // once per result: turning the review off from the review itself must not
  // insert the text under the user's edits. A failed cleanup is always shown,
  // so the user learns why the text was not cleaned up.
  const skipReview = useSkipReview(agentId);
  const setSkipReviewFor = useVoicePrefsStore((state) => state.setSkipReview);
  const result = dictation.state.kind === 'result' ? dictation.state : null;
  const insertDirectly = skipReview && result?.cleanupError === undefined;
  const [reviewed, setReviewed] = useState<DictationState | null>(null);
  const autoInserting = insertDirectly && result !== null && result !== reviewed;
  // A layout effect, so a result never paints as a dialog before it lands.
  useLayoutEffect(() => {
    if (result === null || result === reviewed) return;
    const text = carried === null ? result.text : spliceTranscript(carried, result.text).text;
    if (insertDirectly && insertTranscript(text)) return;
    setReviewed(result);
  }, [result, reviewed, insertDirectly, carried, insertTranscript]);
  const dictationShown = (dictation.state.kind !== 'idle' || carried !== null) && !autoInserting;
  const refetchCloud = cloudQuery.refetch;

  return {
    agentId,
    agentName,
    /** Who transcribes, for copy like "Transcribing on …". */
    transcriberName: route === 'cloud' ? CLOUD_VOICE_NAME : agentName,
    micMode,
    route,
    cloud,
    cloudChecking: cloudQuery.isFetching,
    recheckCloud: () => refetchCloud(),
    /** The agent has voice input of its own (whatever its install state). */
    localSupported: capability !== null,
    /** The agent did not answer the capability probe. */
    localProbeFailed: probeFailed,
    status: status.data,
    statusLoadFailed: statusFailed,
    dictation,
    insertTranscript,
    skipReview,
    setSkipReview: (skip: boolean) => {
      setSkipReviewFor(agentId, skip);
    },
    sessionKind,
    cleanupAvailable: cleanupKind !== null,
    cleanUp,
    setupOpen,
    closeSetup: () => {
      setSetupOpen(false);
    },
    dictationShown,
    /** A voice dialog is on screen. */
    dialogOpen: setupOpen || dictationShown,
    /** Synchronous on purpose: the recorder opens audio inside this click's user activation. */
    onMicClick: () => {
      if (micMode === 'ready') {
        dictation.toggle();
        return;
      }
      // The cached status may be a minute old: ask again while the dialog opens.
      if (micMode === 'unavailable') void refetchCloud();
      setSetupOpen(true);
    },
  };
}

export type ComposerVoice = ReturnType<typeof useComposerVoice>;
