import type { ReactNode } from 'react';
import type { VoiceStatus } from '../../../protocol/dto/voice';
import type { ComposerMicMode, VoiceRoute } from '../../../voice/voiceEngine';
import { voicePreparingLabel, voiceProgressPercent } from '../../../voice/voiceStatusView';
import { AlertIcon, DownloadIcon, MicIcon, MicOffIcon } from './VoiceIcons';

interface VoiceButtonProps {
  mode: Exclude<ComposerMicMode, 'hidden'>;
  /** Who would transcribe; only the accessible name depends on it. */
  route?: VoiceRoute;
  status: VoiceStatus | undefined;
  disabled: boolean;
  onClick: () => void;
}

/**
 * The toolbar's voice entry. Every state has its own glyph, as on Android,
 * so nothing depends on colour: a mic when ready, a hollow mic when off, a
 * download arrow (with the percentage) while preparing, `!` after a failed
 * install or while cloud voice input cannot record. The accessible name
 * spells the state out.
 */
export function VoiceButton({ mode, route = 'local', status, disabled, onClick }: VoiceButtonProps) {
  let icon: ReactNode;
  let text: string | null = null;
  let label: string;
  switch (mode) {
    case 'ready':
      icon = <MicIcon />;
      label =
        route === 'cloud'
          ? 'Dictate: record speech for Mobaide Cloud to transcribe'
          : 'Dictate: record speech for your computer to transcribe';
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
    case 'unavailable':
      icon = <AlertIcon />;
      label = 'Cloud voice input can’t record right now. Show details';
      break;
  }
  return (
    <button
      type="button"
      // `unavailable` shares the look of a failed install: the same "!" tone.
      className={`button voice-button voice-button-${mode === 'unavailable' ? 'failed' : mode}`}
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
