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
 * Every field but the `MOBA1` version tag is a placeholder rather than a
 * plausible-looking value, so a user comparing this against their terminal can
 * never mistake the example for something copyable — and so nothing here can be
 * wrong for their deployment. That includes the relay URL, which is the agent's
 * own `PUBLIC_BASE_URL`: unlike the link origin below it is *not* derivable
 * here (the SPA reaches the relay same-origin through a proxy, `baseUrl: '/'`),
 * and a hard-coded `api.mobaide.com` would be exactly the line a self-hosted
 * agent never prints. Not being able to show the right value is a reason to
 * show a placeholder, not a reason to show a wrong one.
 *
 * What carries the recognition is the `[mobaide-agent]` labels and the `MOBA1|`
 * prefix, none of which vary by deployment.
 */
export const PAIRING_HINT_EXAMPLE_PAYLOAD =
  'MOBA1|<relay-url>|<nonce>|<fingerprint>|<expires>';

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
