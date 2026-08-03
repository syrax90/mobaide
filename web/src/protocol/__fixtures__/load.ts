/**
 * Loader for the cross-platform golden fixtures at repo-root
 * `tests/protocol_fixtures/`, modelled on iOS `MobaideTests/Fixtures/FixtureLoader.swift`.
 *
 * iOS reaches the tree through a blue folder reference copied into its test
 * bundle; TypeScript has no bundle, so this reads straight off disk relative to
 * its own module URL. That keeps the fixtures a single shared source of truth
 * rather than a third hand-mirrored copy — the drift debt Android already
 * carries.
 *
 * Test-only. It imports `node:fs`, so every file that uses it must declare
 * `// @vitest-environment node`, and nothing under `src/` outside a `*.test.ts`
 * may import it.
 */
/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs';
import { join, posix } from 'node:path';
import { fileURLToPath } from 'node:url';

/** `web/src/protocol/__fixtures__/` → repo root. */
const REPO_ROOT = fileURLToPath(new URL('../../../../', import.meta.url));

export const FIXTURES_ROOT = join(REPO_ROOT, 'tests', 'protocol_fixtures');

export const PROTOCOL_GOLDEN_PATH = join(REPO_ROOT, 'tests', 'protocol_golden.json');

/** Read and parse a fixture by its path relative to `tests/protocol_fixtures/`. */
export function readFixture<T>(relativePath: string): T {
  const absolute = join(FIXTURES_ROOT, ...relativePath.split('/'));
  return JSON.parse(readFileSync(absolute, 'utf8')) as T;
}

export function readProtocolGolden<T>(): T {
  return JSON.parse(readFileSync(PROTOCOL_GOLDEN_PATH, 'utf8')) as T;
}

/**
 * Every `.json` under `tests/protocol_fixtures/`, as `/`-joined relative paths,
 * sorted. Drives the coverage gate — a fixture file nobody classified is a
 * fixture nobody is testing.
 */
export function listFixtureFiles(directory = FIXTURES_ROOT, prefix = ''): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const relative = prefix === '' ? entry.name : posix.join(prefix, entry.name);
    if (entry.isDirectory()) {
      found.push(...listFixtureFiles(join(directory, entry.name), relative));
    } else if (entry.name.endsWith('.json')) {
      found.push(relative);
    }
  }
  return found.sort();
}

export function hexToBytes(hex: string): Uint8Array {
  if (hex.length % 2 !== 0) {
    throw new Error(`odd-length hex string: ${hex}`);
  }
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i += 1) {
    const byte = Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    if (Number.isNaN(byte)) throw new Error(`invalid hex string: ${hex}`);
    out[i] = byte;
  }
  return out;
}

export function bytesToHex(bytes: Uint8Array): string {
  let out = '';
  for (const byte of bytes) {
    out += byte.toString(16).padStart(2, '0');
  }
  return out;
}

/**
 * Normalise a decoded DTO the way iOS's encoder does before comparison: keys
 * whose value is `undefined` disappear, so "explicitly null on the wire" and
 * "absent from the canonical form" line up.
 */
export function canonicalize(value: unknown): unknown {
  return JSON.parse(JSON.stringify(value)) as unknown;
}

/** One `{name, wire, decoded_canonical?}` case in a `dto/**` fixture file. */
export interface DtoFixtureCase {
  name: string;
  wire: unknown;
  decoded_canonical?: unknown;
}

export interface DtoFixtureFile {
  type: string;
  cases: DtoFixtureCase[];
}
