// @vitest-environment node
//
// Node, not jsdom: this reads the agent's own printer off disk with `node:fs`,
// the way `brandAssets.test.ts` reads the marketing site's assets.
//
// The setup guide shows a user what their terminal will say so they can match
// it against their scrollback. Nothing else fails when the agent reworks those
// lines — the guide still renders, still pastes, still pairs — it just quietly
// stops describing reality, which is the one thing it exists to do.
/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { parseMoba1 } from '../../protocol/pairingPayload';
import {
  PAIRING_HINT_CODE_LABEL,
  PAIRING_HINT_EXAMPLE,
  PAIRING_HINT_EXAMPLE_LINK,
  PAIRING_HINT_EXAMPLE_PAYLOAD,
  PAIRING_HINT_LINK_LABEL,
  SHOW_QR_COMMAND,
} from './setupCopy';

/** `web/src/routes/agents/` → repo root. */
const REPO_ROOT = fileURLToPath(new URL('../../../../', import.meta.url));

const AGENT_ROOT = join(REPO_ROOT, 'mobaide_agent', 'mobaide');

const PAIRING_QR_SOURCE = readFileSync(
  join(AGENT_ROOT, 'agent', 'pairing_qr.py'),
  'utf8',
);

const AGENT_CLI_SOURCE = readFileSync(join(AGENT_ROOT, '__main__.py'), 'utf8');

describe('pairing hint example', () => {
  it.each([
    ['code', PAIRING_HINT_CODE_LABEL],
    ['link', PAIRING_HINT_LINK_LABEL],
  ])('quotes the %s label the agent actually prints', (_name, label) => {
    // `write_pairing_hints` writes these as `out.write("…\n")` literals.
    expect(PAIRING_QR_SOURCE).toContain(`"${label}\\n"`);
  });

  it('names a subcommand the agent CLI actually registers', () => {
    // The guide tells a user to run this on a host whose nonce has expired; a
    // command that no longer parses would strand them with no way to re-print.
    const [binary, subcommand] = SHOW_QR_COMMAND.split(' ');

    expect(binary).toBe('mobaide-agent');
    expect(AGENT_CLI_SOURCE).toContain(`sub.add_parser(\n        "${subcommand}"`);
  });

  it('keeps the agent’s order: label, payload, label, link', () => {
    expect(PAIRING_HINT_EXAMPLE.split('\n')).toEqual([
      PAIRING_HINT_CODE_LABEL,
      PAIRING_HINT_EXAMPLE_PAYLOAD,
      PAIRING_HINT_LINK_LABEL,
      PAIRING_HINT_EXAMPLE_LINK,
    ]);
  });

  it('is inert as pairing input, so a copied example can never be mistaken for one', () => {
    // The placeholders are the whole safety story here: were they filled with
    // plausible values, a user could paste the example and get "expired" or
    // "not a Mobaide pairing code" instead of understanding it was a sample.
    expect(parseMoba1(PAIRING_HINT_EXAMPLE_PAYLOAD)).toBeNull();
  });
});
