import Foundation

/// Chunk `seq` always covers the same samples, so a resend after a gap or a
/// restart re-cuts exactly what was sent before. The audio thread appends
/// while the upload pump reads, hence the lock.
final class PcmBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private var samples: [Int16]
    private var count = 0
    private var capacity: Int

    init(capacitySamples: Int) {
        capacity = max(0, capacitySamples)
        samples = [Int16](repeating: 0, count: capacity)
    }

    var sampleCount: Int { lock.withLock { count } }
    var capacitySamples: Int { lock.withLock { capacity } }
    var isFull: Bool { lock.withLock { count >= capacity } }

    /// Append what fits; returns how many samples were taken.
    @discardableResult
    func append<C: Collection>(_ input: C) -> Int where C.Element == Int16 {
        lock.withLock {
            let taken = min(capacity - count, input.count)
            guard taken > 0 else { return 0 }
            var index = count
            for sample in input.prefix(taken) {
                samples[index] = sample
                index += 1
            }
            count += taken
            return taken
        }
    }

    /// Uploadable chunks: only full ones while recording, plus the tail once stopped.
    func sealedChunks(chunkSamples: Int, final: Bool) -> Int {
        guard chunkSamples > 0 else { return 0 }
        let n = sampleCount
        return final ? (n + chunkSamples - 1) / chunkSamples : n / chunkSamples
    }

    /// `pcm16_b64` of chunk `seq`.
    func payload(seq: Int, chunkSamples: Int) -> String {
        lock.withLock {
            let start = min(seq * chunkSamples, count)
            let end = min(start + chunkSamples, count)
            return Pcm16.base64(Pcm16.encodeLittleEndian(samples[start..<end]))
        }
    }

    /// Apply a lower `max_seconds` from the agent; drops what lies beyond it.
    func shrinkCapacity(_ capacitySamples: Int) {
        lock.withLock {
            let next = max(0, capacitySamples)
            guard next < capacity else { return }
            capacity = next
            count = min(count, next)
        }
    }

    func release() {
        lock.withLock {
            samples = []
            count = 0
            capacity = 0
        }
    }
}
