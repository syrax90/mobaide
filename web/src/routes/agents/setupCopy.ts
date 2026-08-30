/**
 * The strings the first-run setup guide quotes back at the user.
 *
 * A module rather than JSX literals for the reason `pairCopy.ts` is one: these
 * reproduce output that another component of this repo produces, so they can
 * drift, and a test can hold them to it.
 */

/** Re-prints the pairing QR, code and link on a host whose nonce has expired. */
export const SHOW_QR_COMMAND = 'mobaide-agent show-qr';

/**
 * The two labels `pairing_qr.write_pairing_hints` prints, verbatim.
 *
 * Byte-identical to the `out.write(...)` calls in
 * `mobaide_agent/mobaide/agent/pairing_qr.py` — the guide's whole purpose is
 * that a user can match these against their own scrollback, so a silent drift
 * here is worse than no example at all. Pinned by `setupCopy.test.ts`.
 */
export const PAIRING_HINT_CODE_LABEL = '[mobaide-agent] Pairing code (copy this entire line):';

export const PAIRING_HINT_LINK_LABEL = '[mobaide-agent] Or open this link in a browser:';

/**
 * A stand-in for the payload, shaped like the real one.
 *
 * The nonce, fingerprint and expiry are spelled out as placeholders rather than
 * faked with plausible-looking values: a user comparing this against their
 * terminal must never be able to mistake the example for something copyable.
 * The relay host is the production one, because that part *is* what they will
 * see — unlike the link's origin below, the relay URL is the agent's own
 * `PUBLIC_BASE_URL` and is not derivable from this browser.
 */
export const PAIRING_HINT_EXAMPLE_PAYLOAD =
  'MOBA1|https://api.mobaide.com|<nonce>|<fingerprint>|<expires>';

/**
 * The link the agent prints, for a browser served from `webOrigin`.
 *
 * Takes the origin rather than hard-coding `app.mobaide.com`, because the agent
 * does the same: `pairing_qr.pairing_web_origin()` is
 * `MOBAIDE_AGENT_PAIR_WEB_ORIGIN` when set, and that variable means "origin of
 * the web client the printed link points at" — which is *this* client. So a
 * deployment that configured its agent correctly prints the origin the reader
 * is already browsing, and a self-hoster is no longer told to look for a
 * mobaide.com line that never appears in their output. `window.location.origin`
 * carries no trailing slash and the agent `rstrip("/")`s its own, so the two
 * agree without normalising here.
 */
export function pairingLinkPrefix(webOrigin: string): string {
  return `${webOrigin}/pair#`;
}

export function pairingHintExampleLink(webOrigin: string): string {
  return `${pairingLinkPrefix(webOrigin)}<encoded-payload>`;
}

/** The four lines as one block, in the order the agent writes them. */
export function pairingHintExample(webOrigin: string): string {
  return [
    PAIRING_HINT_CODE_LABEL,
    PAIRING_HINT_EXAMPLE_PAYLOAD,
    PAIRING_HINT_LINK_LABEL,
    pairingHintExampleLink(webOrigin),
  ].join('\n');
}
