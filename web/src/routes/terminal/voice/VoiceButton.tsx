import type { VoiceStatus } from '../../../protocol/dto/voice';
import {
  voicePreparingLabel,
  voiceProgressPercent,
  type VoiceMicMode,
} from '../../../voice/voiceStatusView';

interface VoiceButtonProps {
  mode: Exclude<VoiceMicMode, 'hidden'>;
  status: VoiceStatus | undefined;
  disabled: boolean;
  onClick: () => void;
}

/**
 * The toolbar's voice entry. Its text names the state, so nothing depends
 * on colour: `Dictate` when ready, `Voice` (muted) when off, the install
 * percentage while preparing, `Voice !` after a failed install.
 */
export function VoiceButton({ mode, status, disabled, onClick }: VoiceButtonProps) {
  let text: string;
  let label: string;
  switch (mode) {
    case 'ready':
      text = 'Dictate';
      label = 'Dictate: record speech for your computer to transcribe';
      break;
    case 'off':
      text = 'Voice';
      label = 'Voice input is off. Set it up';
      break;
    case 'preparing': {
      const percent = status === undefined ? null : voiceProgressPercent(status);
      text = percent === null ? 'Voice…' : `Voice ${String(percent)}%`;
      label = status === undefined ? 'Preparing voice input' : voicePreparingLabel(status);
      break;
    }
    case 'failed':
      text = 'Voice !';
      label = 'Voice input needs attention. Show details';
      break;
  }
  return (
    <button
      type="button"
      className={`button voice-button voice-button-${mode}`}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {text}
    </button>
  );
}
