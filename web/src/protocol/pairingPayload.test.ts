// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { readFixture } from './__fixtures__/load';
import {
  decodePairingFragment,
  encodePairingFragment,
  normalizeRelayUrl,
  parseMoba1,
  readPairingInput,
} from './pairingPayload';

interface ParseCase {
  name: string;
  raw: string;
  expect: {
    version: string;
    relay_url: string;
    nonce: string;
    trust_key_fp: string;
    exp_unix: number;
  } | null;
}

interface LinkCase {
  name: string;
  payload: string;
  origin: string;
  expect: string;
}

const fixture = readFixture<{ parse_cases: ParseCase[]; link_cases: LinkCase[] }>(
  'pairing/qr_payload.json',
);

/** The agent puts the payload after `#`; everything before it is the origin. */
const fragmentOf = (link: string): string => link.slice(link.indexOf('#') + 1);

describe('MOBA1 payload golden fixtures', () => {
  it('has cases to run', () => {
    expect(fixture.parse_cases.length).toBeGreaterThanOrEqual(20);
    expect(fixture.link_cases.length).toBeGreaterThanOrEqual(4);
  });

  it.each(fixture.parse_cases)('parses $name', (testCase) => {
    const parsed = parseMoba1(testCase.raw);
    const expected = testCase.expect;

    if (expected === null) {
      expect(parsed).toBeNull();
      return;
    }
    expect(parsed).toEqual({
      version: expected.version,
      relayUrl: expected.relay_url,
      nonce: expected.nonce,
      trustKeyFp: expected.trust_key_fp,
      expUnix: expected.exp_unix,
    });
  });

  it.each(fixture.link_cases)('reads the whole $name link as input', (testCase) => {
    // What a user actually pastes: the clickable line, not the bare fragment.
    expect(readPairingInput(testCase.expect)).toBe(testCase.payload);
  });

  it.each(fixture.link_cases)('encodes the $name fragment', (testCase) => {
    // The same bytes the agent prints, from the same golden: padding stripped,
    // and a non-ASCII relay URL encoded as UTF-8 rather than per code unit.
    expect(encodePairingFragment(testCase.payload)).toBe(fragmentOf(testCase.expect));
  });
});

describe('encodePairingFragment', () => {
  it('round-trips every golden payload through the decoder', () => {
    // The setup guide hands a pasted payload to `/pair` by navigating to a
    // fragment; a mismatch between the two halves would turn a valid paste
    // into "that is not a Mobaide pairing code" on arrival.
    for (const testCase of fixture.link_cases) {
      expect(decodePairingFragment(encodePairingFragment(testCase.payload))).toBe(
        testCase.payload,
      );
    }
  });

});

describe('readPairingInput', () => {
  const payload = 'MOBA1|https://relay.test|nonce-abc|ffff|1700000000';

  it('survives the newlines a soft-wrapped terminal copy carries', () => {
    // The agent prints the payload on one unbroken line, but the terminal
    // wraps it to the window width and the copy takes the breaks along. No
    // payload field can hold whitespace, so stripping it is lossless.
    const wrapped = 'MOBA1|https://relay.test|nonc\r\ne-abc|ffff|17000\n00000';

    expect(readPairingInput(wrapped)).toBe(payload);
  });

  it('strips the leading and trailing whitespace a paste picks up', () => {
    expect(readPairingInput(`\n  ${payload}\t \n`)).toBe(payload);
  });

  it('accepts a bare fragment', () => {
    const fragment = fragmentOf(fixture.link_cases[0]?.expect ?? '');

    expect(readPairingInput(fragment)).toBe(fixture.link_cases[0]?.payload);
  });

  it('returns null for empty and whitespace-only input', () => {
    expect(readPairingInput('')).toBeNull();
    expect(readPairingInput('   \n\t ')).toBeNull();
  });

  it('returns null for text that is neither a payload nor base64url', () => {
    // A rejected input has to stay rejected all the way to the copy: this is
    // what separates "that is not a Mobaide pairing code" from a crash.
    expect(readPairingInput('https://example.com/promo')).toBeNull();
    expect(readPairingInput('just some words')).toBeNull();
  });
});

describe('decodePairingFragment', () => {
  it('accepts a literal payload fragment, with or without the hash', () => {
    const payload = 'MOBA1|https://relay.test|n|f|100';

    expect(decodePairingFragment(`#${payload}`)).toBe(payload);
    expect(decodePairingFragment(payload)).toBe(payload);
  });

  it('accepts a percent-encoded literal payload fragment', () => {
    // A browser address bar re-encodes `|` when the fragment is typed by hand.
    expect(decodePairingFragment('#MOBA1%7Cr%7Cn%7Cf%7C100')).toBe('MOBA1|r|n|f|100');
  });

  it('accepts padded base64url as well as the stripped form the agent prints', () => {
    // `MOBA1|r|n|f|1` → 13 bytes → one `=` of padding.
    expect(decodePairingFragment('#TU9CQTF8cnxufGZ8MQ==')).toBe('MOBA1|r|n|f|1');
    expect(decodePairingFragment('#TU9CQTF8cnxufGZ8MQ')).toBe('MOBA1|r|n|f|1');
  });

  it('returns null for bytes that are not valid UTF-8', () => {
    // `//8` is 0xFF 0xFF — a lone continuation byte pair. Decoding non-fatally
    // would yield U+FFFD noise that then fails the parse under the wrong copy.
    expect(decodePairingFragment('#__8')).toBeNull();
  });
});

describe('normalizeRelayUrl', () => {
  it.each([
    ['https://Relay.TEST', 'https://relay.test'],
    // Every trailing slash, not just one: Android's `TrustKeyPinning.normalize`
    // does the same, and a pin keyed on `//` would silently re-TOFU.
    ['https://relay.test///', 'https://relay.test'],
    ['  https://relay.test/  ', 'https://relay.test'],
    ['https://relay.test/base/', 'https://relay.test/base'],
  ])('maps %s to %s', (input, expected) => {
    expect(normalizeRelayUrl(input)).toBe(expected);
  });
});
