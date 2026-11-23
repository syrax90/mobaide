/**
 * Copy of cloud voice input, shared by the chooser, the settings and the
 * dictation dialog. Android `CloudVoiceCopy` and iOS `CloudVoiceCopy` hold
 * the same text.
 */
import type { CloudVoice } from '../protocol/dto/cloudVoice';
import { formatAllowance } from './voiceEngine';

export const CLOUD_VOICE_NAME = 'Mobaide Cloud';
export const LOCAL_VOICE_NAME = 'On this computer';

export const CLOUD_VOICE_SUMMARY = 'Works right away. Nothing to install on your computer.';

export function cloudVoiceDetails(cloud: CloudVoice): string {
  return (
    'Your recording goes over an encrypted connection to Mobaide’s speech service, which ' +
    'turns it into text. The audio is not kept. The text is kept for a short time to ' +
    `prevent abuse. Up to ${formatAllowance(cloud.limits.max_seconds)} per recording and ` +
    `${formatAllowance(cloud.usage.limit_seconds)} per day.`
  );
}

export function localVoiceSummary(agentName: string): string {
  return `Private: ${agentName} turns your speech into text itself.`;
}

export const VOICE_REVIEW_NOTE =
  'The text appears for you to review and is never sent on its own.';
