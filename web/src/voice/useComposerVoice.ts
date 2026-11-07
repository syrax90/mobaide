import { useCallback, useLayoutEffect, useState } from 'react';
import type { DictationState } from './dictationController';
import { spliceTranscript, useAgentName, useDictation } from './useDictation';
import { useVoiceCapability, useVoiceStatus } from './useVoiceStatus';
import { useMicHidden, useSkipReview, useVoicePrefsStore } from './voicePrefsStore';
import { voiceMicMode } from './voiceStatusView';

interface ComposerVoiceOptions {
  /**
   * Puts a transcript where the user types; false when the screen refused it.
   * Keep it stable (`useCallback`): a result is decided against it.
   */
  insert: (text: string) => boolean;
}

/**
 * Voice input of a session screen: the mic's state, the dictation and its
 * review. `ComposerVoiceDialogs` renders the dialogs from the result.
 */
export function useComposerVoice(agentId: string, sessionId: string, { insert }: ComposerVoiceOptions) {
  // The mic shows only on an agent that advertises `capabilities.voice`,
  // styled by its live status (`voice.changed` arrives on the agent channel
  // `useSessionGone` already holds open).
  const capability = useVoiceCapability(agentId).data ?? null;
  const status = useVoiceStatus(agentId, capability !== null);
  const micHidden = useMicHidden(agentId);
  const micMode = voiceMicMode(capability, status.data, micHidden);
  // A capability only while voice is ready: any other state disposes the
  // controller, which cancels a recording the agent can no longer take.
  const dictation = useDictation(agentId, sessionId, micMode === 'ready' ? capability : null);
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
  // insert the text under the user's edits.
  const skipReview = useSkipReview(agentId);
  const setSkipReviewFor = useVoicePrefsStore((state) => state.setSkipReview);
  const result = dictation.state.kind === 'result' ? dictation.state : null;
  const [reviewed, setReviewed] = useState<DictationState | null>(null);
  const autoInserting = skipReview && result !== null && result !== reviewed;
  // A layout effect, so a result never paints as a dialog before it lands.
  useLayoutEffect(() => {
    if (result === null || result === reviewed) return;
    const text = carried === null ? result.text : spliceTranscript(carried, result.text).text;
    if (skipReview && insertTranscript(text)) return;
    setReviewed(result);
  }, [result, reviewed, skipReview, carried, insertTranscript]);
  const dictationShown = (dictation.state.kind !== 'idle' || carried !== null) && !autoInserting;

  return {
    agentId,
    agentName,
    micMode,
    status: status.data,
    statusLoadFailed: status.isError,
    dictation,
    insertTranscript,
    skipReview,
    setSkipReview: (skip: boolean) => {
      setSkipReviewFor(agentId, skip);
    },
    setupOpen,
    closeSetup: () => {
      setSetupOpen(false);
    },
    dictationShown,
    /** A voice dialog is on screen. */
    dialogOpen: setupOpen || dictationShown,
    /** Synchronous on purpose: the recorder opens audio inside this click's user activation. */
    onMicClick: () => {
      if (micMode === 'ready') dictation.toggle();
      else setSetupOpen(true);
    },
  };
}

export type ComposerVoice = ReturnType<typeof useComposerVoice>;
