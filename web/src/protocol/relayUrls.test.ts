// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { readFixture } from './__fixtures__/load';
import {
  agentRestBaseUrl,
  buildWebSocketUrl,
  mapToRelayPath,
  type ConnectionMode,
} from './relayUrls';

interface PathCase {
  path: string;
  agent_id: string;
  expect: string;
}

interface UrlCase {
  name: string;
  base: string;
  mode: ConnectionMode;
  path: string;
  agent_id?: string;
  token: string;
  expect: string;
}

interface RestBaseCase {
  name: string;
  mode: ConnectionMode;
  agent_url?: string | null;
  relay_url?: string | null;
  agent_id?: string | null;
  expect: string | null;
}

interface RelayMappingFixture {
  path_cases: PathCase[];
  url_cases: UrlCase[];
  rest_base_cases: RestBaseCase[];
}

const fixture = readFixture<RelayMappingFixture>('relay/relay_mapping.json');

describe('relay mapping golden fixtures', () => {
  it('has cases to run', () => {
    expect(fixture.path_cases.length).toBeGreaterThan(0);
    expect(fixture.url_cases.length).toBeGreaterThan(0);
    expect(fixture.rest_base_cases.length).toBeGreaterThan(0);
  });

  it.each(fixture.path_cases)('rewrites $path', (pathCase) => {
    expect(mapToRelayPath(pathCase.path, pathCase.agent_id)).toBe(pathCase.expect);
  });

  it.each(fixture.url_cases)('builds $name', (urlCase) => {
    expect(
      buildWebSocketUrl({
        base: urlCase.base,
        mode: urlCase.mode,
        path: urlCase.path,
        agentId: urlCase.agent_id,
        token: urlCase.token,
      }),
    ).toBe(urlCase.expect);
  });

  it.each(fixture.rest_base_cases)('resolves $name', (restCase) => {
    expect(
      agentRestBaseUrl({
        mode: restCase.mode,
        agentUrl: restCase.agent_url,
        relayUrl: restCase.relay_url,
        agentId: restCase.agent_id,
      }),
    ).toBe(restCase.expect);
  });
});

describe('relay URL rules the fixtures imply but do not spell out', () => {
  it('leaves a base64url token untouched instead of percent-encoding it', () => {
    // URLSearchParams would turn `+` into `%2B` and the relay would reject the
    // JWT — hence the deliberate string concatenation in buildWebSocketUrl.
    const url = buildWebSocketUrl({
      base: 'https://relay.example.com',
      mode: 'direct',
      path: 'ws/user-events',
      token: 'a.b+c/d=',
    });
    expect(url).toBe('wss://relay.example.com/ws/user-events?token=a.b+c/d=');
  });

  it('trims a trailing slash off the base, matching iOS', () => {
    expect(
      buildWebSocketUrl({
        base: 'https://relay.example.com/',
        mode: 'relay',
        path: 'ws/events',
        agentId: 'A1',
        token: 't',
      }),
    ).toBe('wss://relay.example.com/relay/ws/events/A1?token=t');
  });

  it('refuses a relay URL with no agent id', () => {
    expect(
      buildWebSocketUrl({
        base: 'https://relay.example.com',
        mode: 'relay',
        path: 'ws/events',
        agentId: null,
        token: 't',
      }),
    ).toBeNull();
  });
});
