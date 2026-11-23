import { describe, expect, it } from 'vitest';
import { cloudVoiceSchema, type CloudVoice } from '../protocol/dto/cloudVoice';
import { voiceStatusSchema, type VoiceStatus } from '../protocol/dto/voice';
import { cloudVoiceOffered, composerMicMode, voiceRoute } from './voiceEngine';

const AGENT = 'a1b2c3d4e5f6';

const cloud = (state: string, engines: Record<string, string> = {}): CloudVoice =>
  cloudVoiceSchema.parse({
    state,
    limits: { sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 },
    usage: { used_seconds: 0, limit_seconds: 1800, resets_at: '2026-11-23T00:00:00Z' },
    engines,
  });

const localStatus = (enabled: boolean): VoiceStatus =>
  voiceStatusSchema.parse({
    state: enabled ? 'ready' : 'not_installed',
    enabled,
    repairing: false,
    model: { id: 'm', name: 'M', languages: ['en'], download_bytes: 1, disk_bytes: 1, ram_bytes: 1 },
    progress: null,
    host: { recommendation: 'ok', reasons: [] },
    speed_factor: null,
    error: null,
  });

describe('cloudVoiceOffered', () => {
  it.each([
    ['ready', true],
    ['unavailable', true],
    ['limit_reached', true],
    ['off', false],
    ['some_future_state', false],
  ])('reads %s as offered: %s', (state, offered) => {
    expect(cloudVoiceOffered(cloud(state))).toBe(offered);
  });

  it('is not offered before the status loads', () => {
    expect(cloudVoiceOffered(undefined)).toBe(false);
  });
});

describe('voiceRoute', () => {
  it.each([undefined, cloud('off', { [AGENT]: 'cloud' }), cloud('some_future_state', { [AGENT]: 'cloud' })])(
    'stays on the computer while cloud is not offered, whatever was chosen (%#)',
    (status) => {
      expect(voiceRoute(status, AGENT, undefined)).toBe('local');
      expect(voiceRoute(status, AGENT, localStatus(false))).toBe('local');
    },
  );

  it.each(['cloud', 'local'] as const)('follows the %s choice stored for the agent', (engine) => {
    const status = cloud('ready', { [AGENT]: engine });

    // The choice wins over what the agent's own voice says, either way.
    expect(voiceRoute(status, AGENT, localStatus(true))).toBe(engine);
    expect(voiceRoute(status, AGENT, localStatus(false))).toBe(engine);
    expect(voiceRoute(status, AGENT, undefined)).toBe(engine);
  });

  it.each(['unavailable', 'limit_reached'])(
    'keeps a cloud choice while cloud is %s, so the mic can say why',
    (state) => {
      expect(voiceRoute(cloud(state, { [AGENT]: 'cloud' }), AGENT, localStatus(true))).toBe('cloud');
    },
  );

  it('keeps an agent whose own voice is already enabled on the computer', () => {
    expect(voiceRoute(cloud('ready'), AGENT, localStatus(true))).toBe('local');
  });

  it('asks when nothing was chosen and the agent has no voice enabled', () => {
    expect(voiceRoute(cloud('ready'), AGENT, localStatus(false))).toBe('choose');
    expect(voiceRoute(cloud('ready'), AGENT, undefined)).toBe('choose');
  });

  it('reads the choice of another agent, or an engine it does not know, as no choice', () => {
    expect(voiceRoute(cloud('ready', { other: 'cloud' }), AGENT, undefined)).toBe('choose');
    expect(voiceRoute(cloud('ready', { [AGENT]: 'remote' }), AGENT, undefined)).toBe('choose');
    expect(voiceRoute(cloud('ready', { [AGENT]: 'remote' }), AGENT, localStatus(true))).toBe('local');
  });
});

describe('composerMicMode', () => {
  it.each(['hidden', 'off', 'preparing', 'failed', 'ready'] as const)(
    'shows the on-computer mic as it is (%s) on the local route',
    (localMode) => {
      expect(composerMicMode('local', cloud('ready'), localMode, false)).toBe(localMode);
    },
  );

  it.each([
    ['ready', 'ready'],
    ['unavailable', 'unavailable'],
    ['limit_reached', 'unavailable'],
  ] as const)('shows a cloud mic as %s -> %s, whatever the on-computer mic is', (state, mode) => {
    expect(composerMicMode('cloud', cloud(state), 'hidden', false)).toBe(mode);
    expect(composerMicMode('cloud', cloud(state), 'failed', false)).toBe(mode);
  });

  it('offers the chooser with an off mic while nothing was chosen', () => {
    expect(composerMicMode('choose', cloud('ready'), 'hidden', false)).toBe('off');
  });

  it.each(['cloud', 'choose'] as const)('hides the %s mic once the user hid it', (route) => {
    expect(composerMicMode(route, cloud('ready'), 'ready', true)).toBe('hidden');
  });
});
