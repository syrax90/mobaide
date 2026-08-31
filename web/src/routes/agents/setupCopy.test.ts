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
  PAIRING_HINT_EXAMPLE_PAYLOAD,
  PAIRING_HINT_LINK_LABEL,
  SHOW_QR_COMMAND,
  pairingHintExample,
  pairingHintExampleLink,
  pairingLinkPrefix,
} from './setupCopy';

/** A self-hosted deployment, i.e. not the origin the agent falls back to. */
const SELF_HOSTED = 'https://mobaide.internal.example';

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
    expect(pairingHintExample(SELF_HOSTED).split('\n')).toEqual([
      PAIRING_HINT_CODE_LABEL,
      PAIRING_HINT_EXAMPLE_PAYLOAD,
      PAIRING_HINT_LINK_LABEL,
      pairingHintExampleLink(SELF_HOSTED),
    ]);
  });

  it('quotes the reader’s own origin, never the agent’s hard-coded default', () => {
    // `MOBAIDE_AGENT_PAIR_WEB_ORIGIN` means "origin of the web client the
    // printed link points at", so a self-hoster that set it sees their own
    // origin in their scrollback — and would find no `app.mobaide.com` line
    // to match against a guide that hard-coded one.
    expect(pairingLinkPrefix(SELF_HOSTED)).toBe(`${SELF_HOSTED}/pair#`);
    expect(pairingHintExample(SELF_HOSTED)).not.toContain('app.mobaide.com');
  });

  it('names no host the reader’s own deployment might not use', () => {
    // The link origin is derivable and so is rendered live; the payload's
    // relay URL is the agent's `PUBLIC_BASE_URL`, which this browser cannot
    // see at all (the SPA proxies same-origin). Neither may be pinned to a
    // mobaide.com literal, or a self-hoster is told to match a line their
    // agent never printed.
    expect(pairingHintExample(SELF_HOSTED)).not.toContain('mobaide.com');
    expect(PAIRING_HINT_EXAMPLE_PAYLOAD).toContain('<relay-url>');
  });

  it('builds the same link shape as the agent’s format_pairing_link', () => {
    // `f"{pairing_web_origin()}/pair#{fragment}"`, and the origin is
    // `rstrip("/")`-ed there exactly as `window.location.origin` arrives here.
    expect(PAIRING_QR_SOURCE).toContain('f"{pairing_web_origin()}/pair#{fragment}"');
    expect(pairingLinkPrefix('https://app.mobaide.com')).toBe(
      'https://app.mobaide.com/pair#',
    );
  });

  it('is inert as pairing input, so a copied example can never be mistaken for one', () => {
    // The placeholders are the whole safety story here: were they filled with
    // plausible values, a user could paste the example and get "expired" or
    // "not a Mobaide pairing code" instead of understanding it was a sample.
    expect(parseMoba1(PAIRING_HINT_EXAMPLE_PAYLOAD)).toBeNull();
  });
});
