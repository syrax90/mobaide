// @vitest-environment node
import { describe, expect, it } from 'vitest';
import type { z } from 'zod';
import { COVERED_FIXTURES } from './__fixtures__/coverage';
import { canonicalize, readFixture, type DtoFixtureFile } from './__fixtures__/load';
import { agentVersionSchema } from './dto/agentVersion';
import {
  autoScanInstanceSchema,
  autoScanListResponseSchema,
} from './dto/autoscan';
import { chatStoredSessionsResponseSchema } from './dto/chatSessions';
import { createCustomAgentTypeRequestSchema } from './dto/customAgentTypes';
import { busyByResponseSchema, busyHolderSchema } from './dto/gitConflict';
import { gitJobSchema } from './dto/gitJob';
import { autoScanFilterSchema, projectInfoSchema } from './dto/projects';
import { sessionKindSchema, sessionResponseSchema } from './dto/session';
import {
  antigravityOptionsSchema,
  claudeOptionsSchema,
  codexOptionsSchema,
  copilotOptionsSchema,
  createSessionRequestSchema,
} from './dto/sessionCreate';
import {
  customAgentTypeSchema,
  customTypeDeletedSchema,
  firstAgentPairedSchema,
  sessionModeOrderSchema,
  templateDeletedSchema,
  templateSchema,
} from './dto/userEvents';
import { voiceStatusSchema } from './dto/voice';

/**
 * Fixture `type` → the schema that owns it, the TypeScript analogue of iOS's
 * `FixtureRegistry.all`. Scope is the realtime layer; `__fixtures__/coverage.ts`
 * records who owns everything else.
 */
const REGISTRY: Readonly<Record<string, z.ZodType>> = {
  SessionKind: sessionKindSchema,
  SessionResponse: sessionResponseSchema,
  CreateSessionRequest: createSessionRequestSchema,
  ClaudeOptionsModel: claudeOptionsSchema,
  CodexOptionsModel: codexOptionsSchema,
  AntigravityOptionsModel: antigravityOptionsSchema,
  CopilotOptionsModel: copilotOptionsSchema,
  VersionInfoResponse: agentVersionSchema,
  CreateCustomAgentTypeRequest: createCustomAgentTypeRequestSchema,
  ProjectInfo: projectInfoSchema,
  AutoScanFilterDto: autoScanFilterSchema,
  AutoScanInstanceDto: autoScanInstanceSchema,
  AutoScanListResponse: autoScanListResponseSchema,
  BusyByResponse: busyByResponseSchema,
  BusyHolder: busyHolderSchema,
  GitJobResponse: gitJobSchema,
  TemplateDto: templateSchema,
  TemplateDeletedPayload: templateDeletedSchema,
  CustomAgentTypeDto: customAgentTypeSchema,
  CustomTypeDeletedPayload: customTypeDeletedSchema,
  FirstAgentPairedPayload: firstAgentPairedSchema,
  SessionModeOrderDto: sessionModeOrderSchema,
  VoiceStatusResponse: voiceStatusSchema,
  ChatStoredSessionsResponse: chatStoredSessionsResponseSchema,
};

const dtoFiles = COVERED_FIXTURES.filter((path) => path.startsWith('dto/'));

describe('DTO golden fixtures', () => {
  it('covers exactly the types the registry declares', () => {
    const types = dtoFiles.map((path) => readFixture<DtoFixtureFile>(path).type);
    expect([...types].sort()).toEqual(Object.keys(REGISTRY).sort());
  });

  it.each(dtoFiles)('round-trips %s', (path) => {
    const file = readFixture<DtoFixtureFile>(path);
    const schema = REGISTRY[file.type];
    expect(schema, `${path} references unregistered type ${file.type}`).toBeDefined();
    expect(file.cases.length, `${file.type} has no cases`).toBeGreaterThan(0);

    for (const testCase of file.cases) {
      const decoded: unknown = schema?.parse(testCase.wire);
      // Canonicalising both sides drops `undefined` keys, so "explicitly null on
      // the wire" lines up with "absent from the canonical form" — the same rule
      // iOS gets for free from an encoder that omits nil.
      expect(canonicalize(decoded), `${file.type}.${testCase.name}`).toEqual(
        canonicalize(testCase.decoded_canonical ?? testCase.wire),
      );
    }
  });
});

/**
 * TypeScript-only, deliberately: the shared fixtures are round-tripped by the
 * iOS registry too, and Swift's `Codable` drops unknown keys on re-encode. An
 * unknown-key case added to a shared file would break the iOS build.
 */
describe('unknown wire data survives decoding', () => {
  it('preserves keys a newer server added', () => {
    const decoded = templateSchema.parse({
      template_id: 'a1b2c3d4e5f6',
      name: 'Deploy checklist',
      body: 'Run the deploy checklist.',
      is_favorite: false,
      created_at: 'c',
      updated_at: 'u',
      version: 1,
      colour: 'red',
      nested: { added_later: true },
    });
    expect(decoded).toMatchObject({ colour: 'red', nested: { added_later: true } });
  });

  it('preserves an unknown session kind verbatim rather than coercing it', () => {
    expect(sessionKindSchema.parse('some_future_kind')).toBe('some_future_kind');
    const session = sessionResponseSchema.parse({
      session_id: 's',
      state: 'running',
      created_at: 'c',
      kind: 'quantum',
    });
    expect(session.kind).toBe('quantum');
  });

  it('falls back to claude only for an explicit null kind', () => {
    const session = sessionResponseSchema.parse({
      session_id: 's',
      state: 'running',
      created_at: 'c',
      kind: null,
    });
    expect(session.kind).toBe('claude');
  });

  it('rejects a payload whose required fields are missing', () => {
    // The bridge drops such an event instead of invalidating on it, matching
    // both mobile clients.
    expect(templateSchema.safeParse({ template_id: 'only-this' }).success).toBe(false);
  });
});
