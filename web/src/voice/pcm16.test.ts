// @vitest-environment node
/// <reference types="node" />
import { createHash } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { bytesToHex, readFixture } from '../protocol/__fixtures__/load';
import { chunkBytes, quantize } from './pcm16';
import { PcmBuffer } from './pcmBuffer';

interface ExactChunk {
  seq: number;
  bytes_hex: string;
  pcm16_b64: string;
}

interface HashedChunk {
  seq: number;
  byte_length: number;
  b64_length: number;
  b64_sha256: string;
}

type GoldenChunk = ExactChunk | HashedChunk;

interface ChunkCase {
  name: string;
  max_chunk_bytes: number;
  samples?: number[];
  generator?: { multiplier: number; count: number };
  open: GoldenChunk[];
  final: GoldenChunk[];
}

interface Pcm16Fixture {
  sample_rate: number;
  chunk_size: { max_chunk_bytes: number; chunk_bytes: number | null }[];
  chunk_cases: ChunkCase[];
  float_to_pcm16: { cases: { name: string; input: (number | string)[]; expect: number[] }[] };
}

const fixture = readFixture<Pcm16Fixture>('voice/pcm16_chunks.json');

function samplesOf(testCase: ChunkCase): Int16Array {
  if (testCase.samples !== undefined) return Int16Array.from(testCase.samples);
  const { multiplier, count } = testCase.generator ?? { multiplier: 0, count: 0 };
  // Int16Array wraps exactly as the fixture's wrap16 does.
  return Int16Array.from({ length: count }, (_, i) => i * multiplier);
}

/** What the uploader would send, described the way the fixture pins it. */
function describeChunks(buffer: PcmBuffer, chunkSamples: number, final: boolean, like: GoldenChunk[]) {
  const hashed = like.some((chunk) => 'b64_sha256' in chunk);
  return Array.from({ length: buffer.sealedChunks(chunkSamples, final) }, (_, seq) => {
    const b64 = buffer.payload(seq, chunkSamples);
    const bytes = Uint8Array.from(Buffer.from(b64, 'base64'));
    return hashed
      ? {
          seq,
          byte_length: bytes.length,
          b64_length: b64.length,
          b64_sha256: createHash('sha256').update(b64).digest('hex'),
        }
      : { seq, bytes_hex: bytesToHex(bytes), pcm16_b64: b64 };
  });
}

describe('pcm16 golden vectors', () => {
  it('has cases to run', () => {
    expect(fixture.chunk_size.length).toBeGreaterThan(0);
    expect(fixture.chunk_cases.length).toBeGreaterThan(0);
    expect(fixture.float_to_pcm16.cases.length).toBeGreaterThan(0);
  });

  it.each(fixture.chunk_size)('sizes a chunk for max_chunk_bytes $max_chunk_bytes', (row) => {
    expect(chunkBytes(fixture.sample_rate, row.max_chunk_bytes)).toBe(row.chunk_bytes);
  });

  it.each(fixture.chunk_cases)('chunks $name while recording and after stop', (testCase) => {
    const samples = samplesOf(testCase);
    const bytes = chunkBytes(fixture.sample_rate, testCase.max_chunk_bytes);
    expect(bytes).not.toBeNull();
    const chunkSamples = (bytes ?? 0) / 2;
    const buffer = new PcmBuffer(samples.length);
    buffer.append(samples);

    expect(describeChunks(buffer, chunkSamples, false, testCase.open)).toEqual(testCase.open);
    expect(describeChunks(buffer, chunkSamples, true, testCase.final)).toEqual(testCase.final);
  });

  it.each(fixture.float_to_pcm16.cases)('quantizes $name', (testCase) => {
    const input = testCase.input.map((value) => Number(value));

    expect(Array.from(quantize(input))).toEqual(testCase.expect);
  });
});

describe('PcmBuffer', () => {
  it('truncates at its capacity and reports what it took', () => {
    const buffer = new PcmBuffer(5);

    expect(buffer.append(Int16Array.from([1, 2, 3]))).toBe(3);
    expect(buffer.append(Int16Array.from([4, 5, 6, 7]))).toBe(2);
    expect(buffer.append(Int16Array.from([8]))).toBe(0);
    expect(buffer.sampleCount).toBe(5);
    expect(buffer.isFull).toBe(true);
  });

  it('drops the samples beyond a lower agent limit', () => {
    const buffer = new PcmBuffer(10);
    buffer.append(Int16Array.from([1, 2, 3, 4, 5, 6]));

    buffer.shrinkCapacity(4);
    buffer.shrinkCapacity(8);

    expect(buffer.sampleCount).toBe(4);
    expect(buffer.capacitySamples).toBe(4);
    expect(buffer.sealedChunks(2, true)).toBe(2);
  });

  it('holds nothing once released', () => {
    const buffer = new PcmBuffer(4);
    buffer.append(Int16Array.from([1, 2]));

    buffer.release();

    expect(buffer.sampleCount).toBe(0);
    expect(buffer.sealedChunks(2, true)).toBe(0);
    expect(buffer.append(Int16Array.from([3]))).toBe(0);
  });
});
