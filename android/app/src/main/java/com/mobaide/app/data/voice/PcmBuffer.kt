package com.mobaide.app.data.voice

/**
 * Chunk `seq` always covers the same samples, so a resend after a gap or a
 * restart re-cuts exactly what was sent before. The recorder thread appends
 * while the upload pump reads, hence the lock.
 */
class PcmBuffer(capacitySamples: Int) {
    private val lock = Any()
    private var samples = ShortArray(capacitySamples.coerceAtLeast(0))
    @Volatile private var count = 0
    @Volatile private var capacity = capacitySamples.coerceAtLeast(0)

    val sampleCount: Int get() = count
    val capacitySamples: Int get() = capacity
    val isFull: Boolean get() = count >= capacity

    /** Append what fits; returns how many samples were taken. */
    fun append(input: ShortArray, length: Int = input.size): Int = synchronized(lock) {
        val taken = minOf(capacity - count, length)
        if (taken <= 0) return 0
        input.copyInto(samples, destinationOffset = count, startIndex = 0, endIndex = taken)
        count += taken
        taken
    }

    /** Uploadable chunks: only full ones while recording, plus the tail once stopped. */
    fun sealedChunks(chunkSamples: Int, final: Boolean): Int {
        if (chunkSamples <= 0) return 0
        val n = count
        return if (final) (n + chunkSamples - 1) / chunkSamples else n / chunkSamples
    }

    /** `pcm16_b64` of chunk [seq]. */
    fun payload(seq: Int, chunkSamples: Int): String = synchronized(lock) {
        val start = (seq.toLong() * chunkSamples).coerceAtMost(count.toLong()).toInt()
        val end = minOf(start + chunkSamples, count)
        Pcm16.base64(Pcm16.encodeLittleEndian(samples, start, end))
    }

    /** Apply a lower `max_seconds` from the agent; drops what lies beyond it. */
    fun shrinkCapacity(capacitySamples: Int) = synchronized(lock) {
        val next = capacitySamples.coerceAtLeast(0)
        if (next < capacity) {
            capacity = next
            count = minOf(count, next)
        }
    }

    fun release() = synchronized(lock) {
        samples = ShortArray(0)
        count = 0
        capacity = 0
    }
}
