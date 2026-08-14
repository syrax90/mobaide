import { describe, expect, it } from 'vitest';
import { SUPPORT_EMAIL, SUPPORT_MAILTO_URL } from './supportLinks';

describe('SUPPORT_MAILTO_URL', () => {
  it('addresses support with a subject carrying no version diagnostic', () => {
    // The URL is handed to whatever mail client the user has, so what matters
    // is the decoded subject they end up staring at — a protocol number there
    // means nothing to the one person who has to read it.
    const url = new URL(SUPPORT_MAILTO_URL);

    expect(url.protocol).toBe('mailto:');
    expect(url.pathname).toBe(SUPPORT_EMAIL);
    expect(new URLSearchParams(url.search).get('subject')).toBe('Mobaide web support');
  });
});
