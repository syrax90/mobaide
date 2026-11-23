import { useMicHidden, useSkipReview, useVoicePrefsStore } from './voicePrefsStore';

/** The per-browser session settings of one agent's voice input: the mic and the review. */
export function VoiceSessionToggles({ agentId }: { agentId: string }) {
  const hidden = useMicHidden(agentId);
  const setMicHidden = useVoicePrefsStore((state) => state.setMicHidden);
  const skipReview = useSkipReview(agentId);
  const setSkipReview = useVoicePrefsStore((state) => state.setSkipReview);
  return (
    <>
      <label className="voice-toggle">
        <input
          type="checkbox"
          checked={!hidden}
          onChange={(event) => {
            setMicHidden(agentId, !event.target.checked);
          }}
        />
        Show the mic button in sessions
      </label>
      <label className="voice-toggle">
        <input
          type="checkbox"
          checked={!skipReview}
          onChange={(event) => {
            setSkipReview(agentId, !event.target.checked);
          }}
        />
        Review dictated text before it is inserted
      </label>
    </>
  );
}
