import { VOICE_STATE, type VoiceStatus } from '../protocol/dto/voice';
import type { VoiceCapability } from '../protocol/dto/agentVersion';

/**
 * How the mic button looks for one agent. Android `VoiceMicMode` and iOS
 * `VoiceMicMode` map the status the same way.
 *
 * - `hidden`: no voice on this agent (too old, `unsupported`, a state this
 *   client does not know, status not loaded yet) or the user hid the mic.
 * - `off`: voice is not installed; a tap opens the enable sheet.
 * - `preparing`: queued, downloading or verifying; a tap explains.
 * - `failed`: the install failed; a tap shows why, with Retry.
 * - `ready`: a tap dictates.
 */
export type VoiceMicMode = 'hidden' | 'off' | 'preparing' | 'failed' | 'ready';

export function voiceMicMode(
  capability: VoiceCapability | null,
  status: VoiceStatus | undefined,
  hiddenByUser: boolean,
): VoiceMicMode {
  if (capability === null || status === undefined) return 'hidden';
  switch (status.state) {
    case VOICE_STATE.notInstalled:
      return hiddenByUser ? 'hidden' : 'off';
    case VOICE_STATE.queued:
    case VOICE_STATE.downloading:
    case VOICE_STATE.verifying:
      return hiddenByUser ? 'hidden' : 'preparing';
    case VOICE_STATE.failed:
      return hiddenByUser ? 'hidden' : 'failed';
    case VOICE_STATE.ready:
      return hiddenByUser ? 'hidden' : 'ready';
    default:
      return 'hidden';
  }
}

/** Whole percent of the install download, or `null` when unknown. */
export function voiceProgressPercent(status: VoiceStatus): number | null {
  const progress = status.progress;
  if (progress === undefined || progress.total_bytes <= 0) return null;
  return Math.min(100, Math.floor((progress.done_bytes / progress.total_bytes) * 100));
}

/** One line for the preparing states, e.g. "Preparing voice… 43%". */
export function voicePreparingLabel(status: VoiceStatus): string {
  if (status.state === VOICE_STATE.queued) return 'Waiting for another voice install on this computer…';
  if (status.state === VOICE_STATE.verifying) return 'Checking voice input…';
  const progress = status.progress;
  if (status.repairing === true && progress !== undefined) {
    const left = Math.max(0, progress.total_bytes - progress.done_bytes);
    return `Updating voice (≈${formatBytes(left)})…`;
  }
  const percent = voiceProgressPercent(status);
  return percent === null ? 'Preparing voice…' : `Preparing voice… ${String(percent)}%`;
}

/** Decimal units, as disk and download sizes are usually quoted. */
export function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${String(Math.round(bytes / 1e6))} MB`;
  if (bytes >= 1e3) return `${String(Math.round(bytes / 1e3))} KB`;
  return `${String(bytes)} B`;
}

/** ISO 639-1 codes as English language names, sorted; unknown codes stay as codes. */
export function languageNames(codes: readonly string[]): string[] {
  let names: Intl.DisplayNames | null = null;
  try {
    names = new Intl.DisplayNames(['en'], { type: 'language' });
  } catch {
    names = null;
  }
  return codes
    .map((code) => {
      try {
        return names?.of(code) ?? code;
      } catch {
        return code;
      }
    })
    .sort((a, b) => a.localeCompare(b));
}

/** "4:05" */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes)}:${String(seconds).padStart(2, '0')}`;
}
