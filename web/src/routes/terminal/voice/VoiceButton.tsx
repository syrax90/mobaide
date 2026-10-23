import type { ReactNode } from 'react';
import type { VoiceStatus } from '../../../protocol/dto/voice';
import {
  voicePreparingLabel,
  voiceProgressPercent,
  type VoiceMicMode,
} from '../../../voice/voiceStatusView';
import { AlertIcon, DownloadIcon, MicIcon, MicOffIcon } from './VoiceIcons';

interface VoiceButtonProps {
  mode: Exclude<VoiceMicMode, 'hidden'>;
  status: VoiceStatus | undefined;
  disabled: boolean;
  onClick: () => void;
}

/**
 * The toolbar's voice entry. Every state has its own glyph, as on Android,
 * so nothing depends on colour: a mic when ready, a hollow mic when off, a
 * download arrow (with the percentage) while preparing, `!` after a failed
 * install. The accessible name spells the state out.
 */
export function VoiceButton({ mode, status, disabled, onClick }: VoiceButtonProps) {
  let icon: ReactNode;
  let text: string | null = null;
  let label: string;
  switch (mode) {
    case 'ready':
      icon = <MicIcon />;
      label = 'Dictate: record speech for your computer to transcribe';
      break;
    case 'off':
      icon = <MicOffIcon />;
      label = 'Voice input is off. Set it up';
      break;
    case 'preparing': {
      const percent = status === undefined ? null : voiceProgressPercent(status);
      icon = <DownloadIcon />;
      text = percent === null ? null : `${String(percent)}%`;
      label = status === undefined ? 'Preparing voice input' : voicePreparingLabel(status);
      break;
    }
    case 'failed':
      icon = <AlertIcon />;
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
      {icon}
      {text !== null && <span aria-hidden="true">{text}</span>}
    </button>
  );
}
