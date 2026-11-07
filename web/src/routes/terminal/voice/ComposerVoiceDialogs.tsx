import type { ComposerVoice } from '../../../voice/useComposerVoice';
import { DictationDialog } from './DictationDialog';
import { VoiceSetupDialog } from './VoiceSetupDialog';

interface ComposerVoiceDialogsProps {
  voice: ComposerVoice;
  canSend: boolean;
  /** False once the session has ended: a new recording could never be inserted. */
  canRecord: boolean;
}

/** The voice setup sheet and the dictation dialog of `useComposerVoice`. */
export function ComposerVoiceDialogs({ voice, canSend, canRecord }: ComposerVoiceDialogsProps) {
  const { dictation } = voice;
  return (
    <>
      {voice.setupOpen && (
        <VoiceSetupDialog
          agentId={voice.agentId}
          agentName={voice.agentName}
          status={voice.status}
          loadFailed={voice.statusLoadFailed}
          onClose={voice.closeSetup}
        />
      )}
      {voice.dictationShown && (
        <DictationDialog
          state={dictation.state}
          carried={dictation.carried}
          agentName={voice.agentName}
          canSend={canSend}
          canRecord={canRecord}
          onStop={dictation.stop}
          onCancel={dictation.cancel}
          onRetry={dictation.retry}
          onRecordAgain={dictation.toggle}
          onDismiss={dictation.dismiss}
          onContinue={dictation.continueFrom}
          onDiscard={dictation.finish}
          onInsert={voice.insertTranscript}
          skipReview={voice.skipReview}
          onSkipReviewChange={voice.setSkipReview}
        />
      )}
    </>
  );
}
