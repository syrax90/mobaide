import { describe, expect, it } from 'vitest';
import type { VoiceCapability } from '../protocol/dto/agentVersion';
import { voiceStatusSchema, type VoiceStatus } from '../protocol/dto/voice';
import {
  formatBytes,
  formatDuration,
  languageNames,
  voiceMicMode,
  voicePreparingLabel,
  voiceProgressPercent,
} from './voiceStatusView';

const CAPABILITY: VoiceCapability = { sampleRate: 16000, maxSeconds: 120, maxChunkBytes: 65536 };

const status = (over: Record<string, unknown> = {}): VoiceStatus =>
  voiceStatusSchema.parse({
    state: 'ready',
    enabled: true,
    repairing: false,
    model: {
      id: 'parakeet',
      name: 'Parakeet',
      languages: ['en'],
      download_bytes: 670_000_000,
      disk_bytes: 700_000_000,
      ram_bytes: 1_200_000_000,
      notice: 'CC-BY-4.0',
    },
    progress: null,
    host: { recommendation: 'ok', reasons: [] },
    speed_factor: null,
    error: null,
    ...over,
  });

describe('voiceMicMode', () => {
  it.each([
    ['not_installed', 'off'],
    ['queued', 'preparing'],
    ['failed', 'failed'],
    ['ready', 'ready'],
    ['unsupported', 'hidden'],
    ['some_future_state', 'hidden'],
  ])('shows %s as %s', (state, mode) => {
    expect(voiceMicMode(CAPABILITY, status({ state }), false)).toBe(mode);
  });

  it.each(['not_installed', 'ready'])(
    'hides the mic in %s once the user hid it',
    (state) => {
      expect(voiceMicMode(CAPABILITY, status({ state }), true)).toBe('hidden');
    },
  );

  it('hides the mic for an agent without voice or before the status loads', () => {
    expect(voiceMicMode(null, status(), false)).toBe('hidden');
    expect(voiceMicMode(CAPABILITY, undefined, false)).toBe('hidden');
  });
});

describe('voicePreparingLabel', () => {
  it('names each preparing state', () => {
    expect(voicePreparingLabel(status({ state: 'queued' }))).toBe(
      'Waiting for another voice install on this computer…',
    );
    expect(voicePreparingLabel(status({ state: 'verifying' }))).toBe('Checking voice input…');
    expect(
      voicePreparingLabel(
        status({ state: 'downloading', progress: { done_bytes: 43_900, total_bytes: 100_000 } }),
      ),
    ).toBe('Preparing voice… 43%');
    expect(voicePreparingLabel(status({ state: 'downloading' }))).toBe('Preparing voice…');
  });

  it('shows what is left of a repair instead of a percentage', () => {
    const repairing = status({
      state: 'downloading',
      repairing: true,
      progress: { done_bytes: 670_000_000, total_bytes: 702_000_000 },
    });

    expect(voicePreparingLabel(repairing)).toBe('Updating voice (≈32 MB)…');
  });
});

describe('voiceProgressPercent', () => {
  it('floors to whole percent, caps at 100 and is unknown without a total', () => {
    expect(voiceProgressPercent(status({ progress: { done_bytes: 999, total_bytes: 1000 } }))).toBe(99);
    expect(voiceProgressPercent(status({ progress: { done_bytes: 1200, total_bytes: 1000 } }))).toBe(100);
    expect(voiceProgressPercent(status({ progress: { done_bytes: 0, total_bytes: 0 } }))).toBeNull();
    expect(voiceProgressPercent(status())).toBeNull();
  });
});

describe('formatting', () => {
  it.each([
    [1_000, '1 KB'],
    [1_200_000_000, '1.2 GB'],
  ])('formats %i bytes in decimal units as %s', (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text);
  });

  it('formats a duration as minutes and padded seconds', () => {
    expect(formatDuration(245_999)).toBe('4:05');
    expect(formatDuration(-5)).toBe('0:00');
  });

  it('names languages in English, sorted by name', () => {
    expect(languageNames(['en', 'de', 'fr'])).toEqual(['English', 'French', 'German']);
  });
});
