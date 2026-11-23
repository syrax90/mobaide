import { expect, test, type Page } from '@playwright/test';
import { json, signIn, stubRelay } from './fixtures';

const AGENT_ID = 'agent-00000001';
const DICTATION_ID = 'c0ffee00c0ffee00c0ffee00c0ffee00';
const TRANSCRIPT = 'Run the tests, then open a pull request.';

const SESSION_ROW = {
  session_id: 's1',
  state: 'running',
  created_at: '2026-09-01T10:00:00+00:00',
  pid: 4242,
  command: ['claude'],
  cwd: '/srv/p1',
  title: 'claude',
  project_name: 'p1',
  kind: 'claude',
  kind_label: null,
  attention: null,
  exit_reason: null,
};

const AGENT_VERSION = {
  name: 'mobaide-agent',
  server_version: '1.5.0',
  protocol_versions: [7],
  min_client_version: 1,
  capabilities: {
    voice: { dictation: true, sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 },
  },
  latest_agent_version: null,
};

/** The agent's own voice input, never enabled: the on-computer option of the chooser. */
const AGENT_VOICE_NOT_INSTALLED = {
  state: 'not_installed',
  enabled: false,
  repairing: false,
  model: {
    id: 'parakeet-tdt-0.6b-v3-int8',
    name: 'NVIDIA Parakeet TDT 0.6B v3 (int8)',
    languages: ['de', 'en', 'fr'],
    download_bytes: 682365212,
    disk_bytes: 733578420,
    ram_bytes: 700000000,
    notice: 'Model: nvidia/parakeet-tdt-0.6b-v3, created by NVIDIA. CC BY 4.0.',
  },
  progress: null,
  host: {
    ram_total_bytes: 17179869184,
    disk_free_bytes: 211072233472,
    cpu_count: 10,
    recommendation: 'ok',
    reasons: [],
  },
  speed_factor: null,
  error: null,
};

const LIMITS = { sample_rate: 16000, max_seconds: 120, max_chunk_bytes: 65536 };

/** `CloudVoiceStatus`, shaped like the `ready_with_choices` golden case. */
function cloudStatus(state: string, engines: Record<string, string>) {
  return {
    state,
    limits: LIMITS,
    usage: { used_seconds: 0, limit_seconds: 1800, resets_at: '2026-11-23T00:00:00Z' },
    engines,
  };
}

/** A steady tone at speech level in place of the host microphone (see voice.spec.ts). */
const FAKE_MICROPHONE = `
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
    configurable: true,
    value: async () => {
      const context = new AudioContext();
      const tone = context.createOscillator();
      tone.frequency.value = 220;
      const gain = context.createGain();
      gain.gain.value = 0.25;
      const out = context.createMediaStreamDestination();
      tone.connect(gain).connect(out);
      tone.start();
      void context.resume();
      return out.stream;
    },
  });
`;

function frame(type: string, text = ''): Buffer {
  return Buffer.concat([Buffer.from(type, 'latin1'), Buffer.from(text, 'utf8')]);
}

interface CloudLog {
  engineSaves: unknown[];
  creates: number;
  audio: number[];
  finishes: number[];
  /** Calls to the agent's own dictation routes; cloud voice must make none. */
  agentDictationCalls: string[];
  ptyFrames: Buffer[];
}

interface CloudServer {
  /** Answers `create`; the default is a new dictation. */
  create?: () => { status: number; body: unknown } | undefined;
  /** Answers `finish`; the default is the transcript. */
  finish?: () => { status: number; body: unknown };
  /** The speech service's state as `GET /api/voice/cloud` reports it. */
  state?: () => string;
}

/**
 * The relay, one running session with a live PTY, an agent whose own voice
 * input is not installed, and the central server's cloud voice routes.
 */
async function stubCloudVoiceTerminal(page: Page, server: CloudServer = {}): Promise<CloudLog> {
  const log: CloudLog = {
    engineSaves: [],
    creates: 0,
    audio: [],
    finishes: [],
    agentDictationCalls: [],
    ptyFrames: [],
  };
  const engines: Record<string, string> = {};
  const status = () => cloudStatus(server.state?.() ?? 'ready', { ...engines });
  await page.addInitScript(FAKE_MICROPHONE);
  await stubRelay(page);
  await page.route('**/proxy/*/api/sessions', (route) => route.fulfill(json([SESSION_ROW])));
  await page.route('**/proxy/*/api/version', (route) => route.fulfill(json(AGENT_VERSION)));
  await page.route('**/api/mobile/refresh', (route) =>
    route.fulfill({ ...json({ detail: 'no' }), status: 401 }),
  );
  await page.route(/\/proxy\/[^/]+\/api\/voice\/status$/, (route) =>
    route.fulfill(json(AGENT_VOICE_NOT_INSTALLED)),
  );
  await page.route(/\/proxy\/[^/]+\/api\/voice\/dictations/, (route) => {
    log.agentDictationCalls.push(route.request().url());
    return route.fulfill({ ...json({ detail: 'not here' }), status: 500 });
  });
  await page.route(/\/api\/voice\/cloud$/, (route) => route.fulfill(json(status())));
  await page.route(/\/api\/voice\/cloud\/engines\/[^/]+$/, (route) => {
    const body = route.request().postDataJSON() as { engine: string };
    log.engineSaves.push({ agent: route.request().url().split('/').at(-1), ...body });
    engines[AGENT_ID] = body.engine;
    return route.fulfill(json(status()));
  });
  await page.route(/\/api\/voice\/cloud\/dictations$/, (route) => {
    log.creates += 1;
    const reply = server.create?.();
    if (reply !== undefined) return route.fulfill({ ...json(reply.body), status: reply.status });
    return route.fulfill(json({ dictation_id: DICTATION_ID, ...LIMITS }));
  });
  await page.route(/\/api\/voice\/cloud\/dictations\/[^/]+\/audio$/, (route) => {
    const body = route.request().postDataJSON() as { seq: number };
    log.audio.push(body.seq);
    return route.fulfill(json({ seq: body.seq, duration_ms: 0 }));
  });
  await page.route(/\/api\/voice\/cloud\/dictations\/[^/]+\/finish$/, (route) => {
    const body = route.request().postDataJSON() as { last_seq: number };
    log.finishes.push(body.last_seq);
    const reply = server.finish?.() ?? {
      status: 200,
      body: { dictation_id: DICTATION_ID, state: 'done', text: TRANSCRIPT, duration_ms: 2048 },
    };
    return route.fulfill({ ...json(reply.body), status: reply.status });
  });
  await page.routeWebSocket(/\/relay\/ws\/events\//, (ws) => {
    ws.send(JSON.stringify({ type: 'sessions.changed', payload: { sessions: [SESSION_ROW] } }));
  });
  await page.routeWebSocket(/\/relay\/ws\/session\//, (ws) => {
    ws.onMessage((message) => {
      if (typeof message !== 'string') log.ptyFrames.push(message);
    });
    ws.send(frame('s', JSON.stringify({ status: 'running', pid: 4242 })));
    ws.send(frame('b'));
    ws.send(frame('o', '\x1b[?2004h$ '));
    ws.send(frame('e'));
  });
  return log;
}

async function openSession(page: Page): Promise<void> {
  await page.goto('/login');
  await signIn(page);
  await expect(page).toHaveURL(/:\d+\/$/);
  await page.goto(`/agents/${AGENT_ID}/sessions/s1`);
}

function cloudDictateButton(page: Page) {
  return page.getByRole('button', { name: /^Dictate: record speech for Mobaide Cloud/ });
}

/** Records until one chunk reached the server, then stops. */
async function recordOnce(page: Page, log: CloudLog): Promise<void> {
  await cloudDictateButton(page).click();
  const recording = page.getByRole('dialog', { name: 'Recording' });
  await expect(recording).toBeVisible();
  await expect.poll(() => log.audio.length, { timeout: 10_000 }).toBeGreaterThanOrEqual(1);
  await recording.getByRole('button', { name: 'Stop' }).click();
}

test('the first mic tap offers Mobaide Cloud preselected, and the dictation goes to the central server', async ({
  page,
}) => {
  const log = await stubCloudVoiceTerminal(page);
  await openSession(page);

  await page.getByRole('button', { name: 'Voice input is off. Set it up' }).click();
  const chooser = page.getByRole('dialog', { name: 'Voice input' });
  await expect(chooser.getByRole('radio', { name: /Mobaide Cloud/ })).toBeChecked();
  await expect(chooser.getByRole('radio', { name: /On this computer/ })).not.toBeChecked();
  // Nothing is recorded or sent before the user confirms.
  expect(log.creates).toBe(0);
  expect(log.engineSaves).toEqual([]);

  await chooser.getByRole('button', { name: 'Continue' }).click();

  await expect(chooser).toBeHidden();
  expect(log.engineSaves).toEqual([{ agent: AGENT_ID, engine: 'cloud' }]);
  expect(log.creates).toBe(0);

  await recordOnce(page, log);

  const review = page.getByRole('dialog', { name: 'Review' });
  await expect(review.getByRole('textbox')).toHaveValue(TRANSCRIPT);
  expect(log.creates).toBe(1);
  expect(log.audio).toEqual(log.audio.map((_, index) => index));
  expect(log.finishes).toEqual([log.audio.at(-1)]);
  expect(log.agentDictationCalls).toEqual([]);

  await review.getByRole('button', { name: 'Insert' }).click();

  await expect(review).toBeHidden();
  // One bracketed paste and no Enter: the user sends it from the prompt.
  const inputs = () =>
    log.ptyFrames.filter((sent) => sent[0] === 0x69).map((sent) => sent.toString('utf8'));
  await expect.poll(inputs).toEqual([`i\x1b[200~${TRANSCRIPT}\x1b[201~`]);
});

test('a speech service that stops answering keeps the recording for Retry', async ({ page }) => {
  let down = true;
  const log = await stubCloudVoiceTerminal(page, {
    state: () => (down && log.finishes.length > 0 ? 'unavailable' : 'ready'),
    finish: () =>
      down
        ? {
            status: 503,
            body: {
              detail: {
                code: 'cloud_unavailable',
                message: 'Cloud voice input is unavailable right now.',
              },
            },
          }
        : {
            status: 200,
            body: { dictation_id: DICTATION_ID, state: 'done', text: TRANSCRIPT, duration_ms: 2048 },
          },
  });
  await openSession(page);
  await page.getByRole('button', { name: 'Voice input is off. Set it up' }).click();
  await page.getByRole('dialog', { name: 'Voice input' }).getByRole('button', { name: 'Continue' }).click();

  await recordOnce(page, log);

  const failed = page.getByRole('dialog', { name: 'Dictation failed' });
  await expect(failed).toContainText(
    'Cloud voice input is unavailable right now. Try again in a moment.',
  );
  // The mic says why it cannot record, instead of looking ready.
  await expect(
    page.getByRole('button', { name: /^Cloud voice input can’t record right now/ }),
  ).toBeVisible();

  down = false;
  await failed.getByRole('button', { name: 'Retry' }).click();

  await expect(page.getByRole('dialog', { name: 'Review' }).getByRole('textbox')).toHaveValue(
    TRANSCRIPT,
  );
  // The same recording, sent again: no second recording and no second upload.
  expect(log.creates).toBe(1);
  expect(log.finishes).toHaveLength(2);
  expect(log.finishes[1]).toBe(log.finishes[0]);
  expect(log.agentDictationCalls).toEqual([]);
});

test('a "dictate more" that fails returns to the review with its text', async ({ page }) => {
  // The first dictation uses up today's allowance: the status says so from
  // then on, and the next dictation is refused.
  const log = await stubCloudVoiceTerminal(page, {
    state: () => (log.finishes.length > 0 ? 'limit_reached' : 'ready'),
    create: () =>
      log.finishes.length > 0
        ? {
            status: 429,
            body: {
              detail: {
                code: 'cloud_limit_reached',
                message: 'Today’s cloud voice input limit is used up.',
              },
            },
          }
        : undefined,
  });
  await openSession(page);
  await page.getByRole('button', { name: 'Voice input is off. Set it up' }).click();
  await page.getByRole('dialog', { name: 'Voice input' }).getByRole('button', { name: 'Continue' }).click();
  await recordOnce(page, log);
  const review = page.getByRole('dialog', { name: 'Review' });
  const edited = `${TRANSCRIPT} Then deploy.`;
  await review.getByRole('textbox').fill(edited);

  await review.getByRole('button', { name: 'Dictate more at the cursor' }).click();

  const failed = page.getByRole('dialog', { name: 'Dictation failed' });
  await expect(failed).toContainText('cloud voice input limit is used up');
  await failed.getByRole('button', { name: 'Close' }).click();
  // The text the user reviewed and edited is still there to insert.
  await expect(review.getByRole('textbox')).toHaveValue(edited);
  expect(log.creates).toBe(2);
});
