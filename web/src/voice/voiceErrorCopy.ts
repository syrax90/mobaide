/**
 * User-facing copy keyed by `error.code`. The agent's own `message` can carry
 * host details (a worker traceback tail), so it is only ever shown behind
 * "Details". Unknown codes get the generic line. Android and iOS
 * `VoiceErrorCopy` hold the same table.
 */
import { VOICE_CODE } from './voiceApi';

/** A dictation that failed; `agent` names the computer. */
export function dictationErrorCopy(code: string, agent: string): string {
  switch (code) {
    case VOICE_CODE.notReady:
      return `Voice input isn't ready on ${agent}.`;
    case VOICE_CODE.unsupported:
      return `${agent} can't run voice input.`;
    case VOICE_CODE.busy:
      return `Voice input is busy on ${agent}. Try again in a moment.`;
    case VOICE_CODE.transcriptionFailed:
      return `Voice can't run on ${agent}. Retry, or reinstall voice in its settings.`;
    case VOICE_CODE.transcriptionTimeout:
      return `${agent} is taking too long to transcribe.`;
    case VOICE_CODE.dictationNotFound:
      return 'The dictation expired on the agent.';
    case VOICE_CODE.network:
      return 'The connection dropped. Your recording is kept.';
    case VOICE_CODE.micPermissionDenied:
      return 'Microphone access is blocked.';
    case VOICE_CODE.micUnavailable:
      return 'No microphone is available.';
    case VOICE_CODE.badAudio:
    case VOICE_CODE.chunkTooLarge:
    case VOICE_CODE.seqGap:
    case VOICE_CODE.dictationFinished:
    case VOICE_CODE.tooLong:
      return `${agent} couldn't read the recording.`;
    default:
      return 'Voice input failed.';
  }
}

/** Why a cleanup failed; the transcript is shown as heard. */
export function cleanupErrorCopy(code: string, agent: string): string {
  switch (code) {
    case VOICE_CODE.cleanupUnavailable:
      return `Claude Code isn't installed on ${agent}.`;
    case VOICE_CODE.cleanupOutdated:
      return `Claude Code on ${agent} is too old. Run "claude update" there.`;
    case VOICE_CODE.cleanupBusy:
      return 'Another cleanup was still running.';
    case VOICE_CODE.cleanupTimeout:
      return 'Claude Code took too long.';
    case VOICE_CODE.network:
      return 'The connection dropped.';
    default:
      return 'Claude Code reported an error.';
  }
}

/** Why an install ended `failed` (`VoiceStatus.error.code`). */
export function installErrorCopy(code: string, repairing: boolean): string {
  switch (code) {
    case 'interrupted':
      return repairing
        ? 'Voice needs a small update on this computer.'
        : 'The voice install was interrupted.';
    case 'network':
      return "The download failed. Check the computer's internet connection.";
    case 'http_error':
      return 'The download server refused the request.';
    case 'hash_mismatch':
    case 'size_mismatch':
      return 'The download was corrupted.';
    case 'disk_full':
      return 'The computer is out of disk space.';
    case 'io_error':
    case 'storage_error':
      return "Voice files couldn't be written on the computer.";
    case 'selftest_failed':
      return 'The speech model failed its self-test on the computer.';
    default:
      return "Voice input couldn't be installed.";
  }
}

/** A refused install / cancel / remove request. */
export function voiceActionErrorCopy(code: string | null): string {
  switch (code) {
    case VOICE_CODE.unsupported:
      return "This computer can't run voice input.";
    case 'install_in_progress':
      return 'Another Mobaide process is changing voice input on this computer.';
    case 'files_in_use':
      return 'Some voice files are still in use. Try again in a minute.';
    case 'storage_error':
      return "Voice files couldn't be written on the computer.";
    default:
      return "The computer didn't accept the change.";
  }
}
