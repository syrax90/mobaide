import { CLOUD_VOICE_STATE, VOICE_ENGINE, type CloudVoice } from '../protocol/dto/cloudVoice';
import type { VoiceStatus } from '../protocol/dto/voice';
import type { VoiceMicMode } from './voiceStatusView';

/**
 * How one agent's dictations are transcribed: by Mobaide's speech service
 * (`cloud`), by the agent's own model (`local`), or not decided yet
 * (`choose`: the mic opens the chooser). Android `VoiceRoute` and iOS
 * `VoiceRoute` resolve it the same way.
 */
export type VoiceRoute = 'cloud' | 'local' | 'choose';

/** Cloud voice input exists for this account, whether or not it works right now. */
export function cloudVoiceOffered(cloud: CloudVoice | undefined): cloud is CloudVoice {
  if (cloud === undefined) return false;
  return (
    cloud.state === CLOUD_VOICE_STATE.ready ||
    cloud.state === CLOUD_VOICE_STATE.unavailable ||
    cloud.state === CLOUD_VOICE_STATE.limitReached
  );
}

/**
 * - Cloud not offered (an older server, the feature off, a state this client
 *   does not know): on-computer voice, exactly as before cloud existed.
 * - The user chose for this agent: that choice.
 * - No choice yet: an agent whose own voice input is already enabled keeps
 *   it; any other agent asks.
 */
export function voiceRoute(
  cloud: CloudVoice | undefined,
  agentId: string,
  localStatus: VoiceStatus | undefined,
): VoiceRoute {
  if (!cloudVoiceOffered(cloud)) return 'local';
  const choice = Object.hasOwn(cloud.engines, agentId) ? cloud.engines[agentId] : undefined;
  if (choice === VOICE_ENGINE.cloud) return 'cloud';
  if (choice === VOICE_ENGINE.local) return 'local';
  return localStatus?.enabled === true ? 'local' : 'choose';
}

/**
 * The session mic. `unavailable` is cloud voice input that cannot record now
 * (the speech service is down, or today's limit is used up); a tap explains.
 */
export type ComposerMicMode = VoiceMicMode | 'unavailable';

export function composerMicMode(
  route: VoiceRoute,
  cloud: CloudVoice | undefined,
  localMode: VoiceMicMode,
  hiddenByUser: boolean,
): ComposerMicMode {
  if (route === 'local') return localMode;
  if (hiddenByUser) return 'hidden';
  if (route === 'choose') return 'off';
  return cloud?.state === CLOUD_VOICE_STATE.ready ? 'ready' : 'unavailable';
}

/** "2 minutes", "30 minutes", "90 seconds". */
export function formatAllowance(seconds: number): string {
  if (seconds % 60 !== 0) return `${String(seconds)} seconds`;
  const minutes = seconds / 60;
  return minutes === 1 ? '1 minute' : `${String(minutes)} minutes`;
}

/**
 * "13 of 30 minutes", in the unit of the limit: minutes (rounded up, so any
 * use shows) for a limit in whole minutes, seconds otherwise.
 */
export function formatUsage(usedSeconds: number, limitSeconds: number): string {
  // The recording that crosses the limit is still transcribed, so the count
  // can pass it; "31 of 30 minutes" would only confuse.
  const used = Math.min(Math.max(0, usedSeconds), limitSeconds);
  if (limitSeconds % 60 !== 0) return `${String(used)} of ${formatAllowance(limitSeconds)}`;
  return `${String(Math.ceil(used / 60))} of ${formatAllowance(limitSeconds)}`;
}

/** The local time the daily limit resets, e.g. "01:00"; empty when unreadable. */
export function resetTimeLabel(resetsAt: string): string {
  const at = new Date(resetsAt);
  if (Number.isNaN(at.getTime())) return '';
  return at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
