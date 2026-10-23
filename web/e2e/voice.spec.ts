import { expect, test, type Page } from '@playwright/test';
import { json, signIn, stubRelay } from './fixtures';

const AGENT_ID = 'agent-00000001';
const DICTATION_ID = '9b1f2c3d4e5f60718293a4b5c6d7e8f9';
const TRANSCRIPT = 'Rename useSessionModel to use the shared hook.';

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

/** The agent's `GET /api/version` with the `capabilities.voice` PROTOCOL.md pins. */
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

/** `VoiceStatusResponse`, shaped like the `not_installed` golden case. */
const NOT_INSTALLED = {
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

/** The `ready` golden case. */
const READY = { ...NOT_INSTALLED, state: 'ready', enabled: true, speed_factor: 35.2 };

/** `1 s` of PCM16 at the agent's 16 kHz: the uploader's chunk size. */
const CHUNK_BYTES = 32_000;

/**
 * The host microphone stays out of it: `getUserMedia` answers a Web Audio
 * stream of a steady tone at speech level, so the real AudioContext, the
 * static capture worklet, the resampler and the uploader all run on it.
 */
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

/** A binary PTY frame: type byte + UTF-8 payload. */
function frame(type: string, text = ''): Buffer {
  return Buffer.concat([Buffer.from(type, 'latin1'), Buffer.from(text, 'utf8')]);
}

interface AgentLog {
  creates: unknown[];
  audio: { seq: number; bytes: Buffer }[];
  finishes: number[];
  deletes: string[];
  statusReads: number;
  /** Binary frames the terminal sent to the PTY. */
  ptyFrames: Buffer[];
}

interface VoiceAgent {
  status: () => unknown;
  /** Overrides the dictation create; the default opens `DICTATION_ID`. */
  create?: () => { status: number; body: unknown };
}

/**
 * The relay, one running session whose PTY socket goes live with bracketed
 * paste on, and the agent's voice routes. Every call the client makes to the
 * agent lands in the returned log.
 */
async function stubVoiceTerminal(page: Page, agent: VoiceAgent): Promise<AgentLog> {
  const log: AgentLog = {
    creates: [],
    audio: [],
    finishes: [],
    deletes: [],
    statusReads: 0,
    ptyFrames: [],
  };
  await page.addInitScript(FAKE_MICROPHONE);
  await stubRelay(page);
  // Registered after `stubRelay`, so these win over its catch-alls.
  await page.route('**/proxy/*/api/sessions', (route) => route.fulfill(json([SESSION_ROW])));
  await page.route('**/proxy/*/api/version', (route) => route.fulfill(json(AGENT_VERSION)));
  await page.route('**/api/mobile/refresh', (route) =>
    route.fulfill({ ...json({ detail: 'no' }), status: 401 }),
  );
  await page.route(/\/proxy\/[^/]+\/api\/voice\/status$/, (route) => {
    log.statusReads += 1;
    return route.fulfill(json(agent.status()));
  });
  await page.route(/\/proxy\/[^/]+\/api\/voice\/dictations$/, (route) => {
    log.creates.push(route.request().postDataJSON());
    const reply = agent.create?.() ?? {
      status: 200,
      body: {
        dictation_id: DICTATION_ID,
        sample_rate: 16000,
        max_seconds: 120,
        max_chunk_bytes: 65536,
      },
    };
    return route.fulfill({ ...json(reply.body), status: reply.status });
  });
  await page.route(/\/proxy\/[^/]+\/api\/voice\/dictations\/[^/]+\/audio$/, (route) => {
    const body = route.request().postDataJSON() as { seq: number; pcm16_b64: string };
    log.audio.push({ seq: body.seq, bytes: Buffer.from(body.pcm16_b64, 'base64') });
    return route.fulfill(json({ seq: body.seq, duration_ms: 0 }));
  });
  await page.route(/\/proxy\/[^/]+\/api\/voice\/dictations\/[^/]+\/finish$/, (route) => {
    const body = route.request().postDataJSON() as { last_seq: number };
    log.finishes.push(body.last_seq);
    return route.fulfill(
      json({ dictation_id: DICTATION_ID, state: 'done', text: TRANSCRIPT, duration_ms: 2048 }),
    );
  });
  await page.route(/\/proxy\/[^/]+\/api\/voice\/dictations\/[^/]+$/, (route) => {
    if (route.request().method() !== 'DELETE') return route.fallback();
    log.deletes.push(route.request().url().split('/').at(-1) ?? '');
    return route.fulfill({ status: 204, body: '' });
  });
  await page.routeWebSocket(/\/relay\/ws\/events\//, (ws) => {
    ws.send(JSON.stringify({ type: 'sessions.changed', payload: { sessions: [SESSION_ROW] } }));
  });
  // State, then a replay whose output turns bracketed paste on: the shape the
  // agent sends on every open, after which the terminal accepts input.
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

function dictateButton(page: Page) {
  return page.getByRole('button', { name: /^Dictate/ });
}

test('records, transcribes on the agent and inserts the text without sending it', async ({
  page,
}) => {
  const log = await stubVoiceTerminal(page, { status: () => READY });
  await openSession(page);

  await dictateButton(page).click();
  const recording = page.getByRole('dialog', { name: 'Recording' });
  await expect(recording).toBeVisible();
  await expect
    .poll(async () => Number(await recording.getByRole('meter').getAttribute('aria-valuenow')))
    .toBeGreaterThan(0);
  // Two full seconds reach the agent while recording continues.
  await expect.poll(() => log.audio.length, { timeout: 10_000 }).toBeGreaterThanOrEqual(2);

  await recording.getByRole('button', { name: 'Stop' }).click();

  const review = page.getByRole('dialog', { name: 'Review' });
  await expect(review.getByRole('textbox')).toHaveValue(TRANSCRIPT);
  expect(log.creates).toEqual([{ session_id: 's1' }]);
  const seqs = log.audio.map((chunk) => chunk.seq);
  expect(seqs).toEqual(seqs.map((_, index) => index));
  expect(log.finishes).toEqual([seqs.at(-1)]);
  const first = log.audio[0]?.bytes ?? Buffer.alloc(0);
  expect(first.length).toBe(CHUNK_BYTES);
  let peak = 0;
  for (let offset = 0; offset < first.length; offset += 2) {
    peak = Math.max(peak, Math.abs(first.readInt16LE(offset)));
  }
  expect(peak).toBeGreaterThan(1_000);

  await review.getByRole('button', { name: 'Insert' }).click();

  await expect(review).toBeHidden();
  // One bracketed paste and no Enter: the user sends it from the prompt.
  const inputs = () =>
    log.ptyFrames.filter((sent) => sent[0] === 0x69).map((sent) => sent.toString('utf8'));
  await expect.poll(inputs).toEqual([`i\x1b[200~${TRANSCRIPT}\x1b[201~`]);
  expect(log.deletes).toEqual([]);
});

test('dictates more from the review, then skips the review once it is turned off', async ({
  page,
}) => {
  const log = await stubVoiceTerminal(page, { status: () => READY });
  await openSession(page);
  const recording = page.getByRole('dialog', { name: 'Recording' });
  const review = page.getByRole('dialog', { name: 'Review' });
  const record = async (): Promise<void> => {
    const sent = log.audio.length;
    await expect(recording).toBeVisible();
    await expect.poll(() => log.audio.length, { timeout: 10_000 }).toBeGreaterThan(sent);
    await recording.getByRole('button', { name: 'Stop' }).click();
  };

  await dictateButton(page).click();
  await record();
  const field = review.getByRole('textbox');
  await expect(field).toHaveValue(TRANSCRIPT);
  // The cursor between two edited words: the next transcript goes there.
  await field.fill('First. Last.');
  await field.press('Home');
  for (let step = 0; step < 'First.'.length; step += 1) await field.press('ArrowRight');

  await review.getByRole('button', { name: 'Dictate more at the cursor' }).click();
  await record();
  await expect(field).toHaveValue(`First. ${TRANSCRIPT} Last.`);
  // The cursor lands just past the inserted words, ready to type on.
  await page.keyboard.type(' Thanks');
  const merged = `First. ${TRANSCRIPT} Thanks Last.`;
  await expect(field).toHaveValue(merged);
  await review.getByRole('checkbox', { name: /without this review/ }).check();
  await review.getByRole('button', { name: 'Insert' }).click();
  await expect(review).toBeHidden();

  await dictateButton(page).click();
  await record();

  // Straight to the prompt: no review, still no Enter.
  const inputs = () =>
    log.ptyFrames.filter((sent) => sent[0] === 0x69).map((sent) => sent.toString('utf8'));
  await expect
    .poll(inputs)
    .toEqual([
      `i\x1b[200~${merged}\x1b[201~`,
      `i\x1b[200~${TRANSCRIPT}\x1b[201~`,
    ]);
  await expect(review).toBeHidden();
});

test('cancelling a recording deletes the dictation on the agent', async ({ page }) => {
  const log = await stubVoiceTerminal(page, { status: () => READY });
  await openSession(page);

  await dictateButton(page).click();
  const recording = page.getByRole('dialog', { name: 'Recording' });
  await expect(recording).toBeVisible();
  await expect.poll(() => log.creates.length).toBe(1);

  await recording.getByRole('button', { name: 'Cancel' }).click();

  await expect(recording).toBeHidden();
  await expect.poll(() => log.deletes).toEqual([DICTATION_ID]);
  expect(log.finishes).toEqual([]);
  await expect(dictateButton(page)).toBeVisible();
});

test('an agent that refuses as voice_not_ready gets its status read again', async ({ page }) => {
  let status: unknown = READY;
  const log = await stubVoiceTerminal(page, {
    status: () => status,
    create: () => {
      // Voice was removed on the computer; the cached status still says ready.
      status = NOT_INSTALLED;
      return {
        status: 409,
        body: { detail: { code: 'voice_not_ready', message: 'Voice input is not set up.' } },
      };
    },
  });
  await openSession(page);
  await expect(dictateButton(page)).toBeVisible();
  const readsBefore = log.statusReads;

  await dictateButton(page).click();

  await expect(page.getByRole('button', { name: 'Voice input is off. Set it up' })).toBeVisible();
  expect(log.statusReads).toBeGreaterThan(readsBefore);
  await expect(dictateButton(page)).toBeHidden();
});
