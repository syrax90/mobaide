import CryptoKit
import XCTest
@testable import Mobaide

private struct Pcm16FixtureFile: Decodable {
    let sample_rate: Int
    let chunk_size: [SizeRow]
    let chunk_cases: [ChunkCase]
    let float_to_pcm16: FloatCases

    struct SizeRow: Decodable {
        let max_chunk_bytes: Int
        let chunk_bytes: Int?
    }

    struct Generator: Decodable {
        let multiplier: Int
        let count: Int
    }

    struct Chunk: Decodable, Equatable {
        let seq: Int
        var bytes_hex: String?
        var pcm16_b64: String?
        var byte_length: Int?
        var b64_length: Int?
        var b64_sha256: String?
    }

    struct ChunkCase: Decodable {
        let name: String
        let max_chunk_bytes: Int
        let samples: [Int]?
        let generator: Generator?
        let open: [Chunk]
        let final: [Chunk]
    }

    struct FloatCases: Decodable {
        let cases: [FloatCase]
    }

    struct FloatCase: Decodable {
        let name: String
        let input: [FloatInput]
        let expect: [Int]
    }

    /// A number, or "NaN" / "Infinity" / "-Infinity" spelled as a string.
    struct FloatInput: Decodable {
        let value: Float

        init(from decoder: any Decoder) throws {
            let container = try decoder.singleValueContainer()
            if let number = try? container.decode(Double.self) {
                value = Float(number)
            } else {
                let text = try container.decode(String.self)
                switch text {
                case "NaN": value = .nan
                case "Infinity": value = .infinity
                case "-Infinity": value = -.infinity
                default: throw DecodingError.dataCorruptedError(in: container, debugDescription: text)
                }
            }
        }
    }
}

/// The iOS arm of `tests/protocol_fixtures/voice/pcm16_chunks.json`, shared
/// with Android, web and the agent's decoder: chunk sizing, the chunks
/// `PcmBuffer` seals while recording and after stop, and the Float32 → PCM16
/// rule the `AVAudioConverter` output goes through.
final class VoicePcm16FixtureTests: XCTestCase {
    private func loadFile() throws -> Pcm16FixtureFile {
        try FixtureLoader.load(Pcm16FixtureFile.self, path: "voice/pcm16_chunks.json")
    }

    private func samples(of chunkCase: Pcm16FixtureFile.ChunkCase) -> [Int16] {
        if let samples = chunkCase.samples { return samples.map { Int16($0) } }
        guard let generator = chunkCase.generator else { return [] }
        return (0..<generator.count).map { Int16(truncatingIfNeeded: $0 * generator.multiplier) }
    }

    /// What the uploader would send, described the way the fixture pins it.
    private func describe(
        _ buffer: PcmBuffer,
        chunkSamples: Int,
        final: Bool,
        hashed: Bool
    ) -> [Pcm16FixtureFile.Chunk] {
        (0..<buffer.sealedChunks(chunkSamples: chunkSamples, final: final)).map { seq in
            let b64 = buffer.payload(seq: seq, chunkSamples: chunkSamples)
            let bytes = Data(base64Encoded: b64) ?? Data()
            if hashed {
                let digest = SHA256.hash(data: Data(b64.utf8)).map { String(format: "%02x", $0) }.joined()
                return Pcm16FixtureFile.Chunk(
                    seq: seq,
                    byte_length: bytes.count,
                    b64_length: b64.count,
                    b64_sha256: digest
                )
            }
            return Pcm16FixtureFile.Chunk(seq: seq, bytes_hex: bytes.hexString, pcm16_b64: b64)
        }
    }

    func testFixtureHasCasesToRun() throws {
        let file = try loadFile()

        XCTAssertFalse(file.chunk_size.isEmpty)
        XCTAssertFalse(file.chunk_cases.isEmpty)
        XCTAssertFalse(file.float_to_pcm16.cases.isEmpty)
    }

    func testChunkBytesMatchesEveryRow() throws {
        let file = try loadFile()

        for row in file.chunk_size {
            XCTAssertEqual(
                Pcm16.chunkBytes(sampleRate: file.sample_rate, maxChunkBytes: row.max_chunk_bytes),
                row.chunk_bytes,
                "max_chunk_bytes \(row.max_chunk_bytes)"
            )
        }
    }

    func testPcmBufferSealsTheGoldenChunksWhileRecordingAndAfterStop() throws {
        let file = try loadFile()

        for chunkCase in file.chunk_cases {
            let pcm = samples(of: chunkCase)
            let bytes = try XCTUnwrap(
                Pcm16.chunkBytes(sampleRate: file.sample_rate, maxChunkBytes: chunkCase.max_chunk_bytes),
                chunkCase.name
            )
            let buffer = PcmBuffer(capacitySamples: pcm.count)
            buffer.append(pcm)
            let hashed = chunkCase.generator != nil

            XCTAssertEqual(
                describe(buffer, chunkSamples: bytes / 2, final: false, hashed: hashed),
                chunkCase.open,
                "\(chunkCase.name) open"
            )
            XCTAssertEqual(
                describe(buffer, chunkSamples: bytes / 2, final: true, hashed: hashed),
                chunkCase.final,
                "\(chunkCase.name) final"
            )
        }
    }

    func testQuantizeFollowsTheFloatRule() throws {
        let file = try loadFile()

        for floatCase in file.float_to_pcm16.cases {
            XCTAssertEqual(
                Pcm16.quantize(floatCase.input.map(\.value)).map(Int.init),
                floatCase.expect,
                floatCase.name
            )
        }
    }
}
