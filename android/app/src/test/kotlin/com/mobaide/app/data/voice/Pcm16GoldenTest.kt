package com.mobaide.app.data.voice

import com.mobaide.app.testing.ProtocolFixtures
import com.mobaide.app.testing.toFixtureHex
import java.security.MessageDigest
import java.util.Base64
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.double
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of `tests/protocol_fixtures/voice/pcm16_chunks.json`, shared
 * with web, iOS and the agent's decoder: chunk sizing, the chunks [PcmBuffer]
 * seals while recording and after stop, and the Float32 → PCM16 rule.
 */
class Pcm16GoldenTest {

    @Serializable
    private data class Chunk(
        val seq: Int,
        val bytes_hex: String? = null,
        val pcm16_b64: String? = null,
        val byte_length: Int? = null,
        val b64_length: Int? = null,
        val b64_sha256: String? = null,
    )

    @Serializable
    private data class Generator(val multiplier: Int, val count: Int)

    @Serializable
    private data class ChunkCase(
        val name: String,
        val max_chunk_bytes: Int,
        val samples: List<Int>? = null,
        val generator: Generator? = null,
        val open: List<Chunk>,
        val final: List<Chunk>,
    )

    @Serializable
    private data class SizeRow(val max_chunk_bytes: Int, val chunk_bytes: Int? = null)

    @Serializable
    private data class FloatCase(val name: String, val input: List<JsonPrimitive>, val expect: List<Int>)

    @Serializable
    private data class FloatCases(val cases: List<FloatCase>)

    @Serializable
    private data class Fixture(
        val sample_rate: Int,
        val chunk_size: List<SizeRow>,
        val chunk_cases: List<ChunkCase>,
        val float_to_pcm16: FloatCases,
    )

    private val fixture: Fixture = ProtocolFixtures.load("voice/pcm16_chunks.json")

    private fun ChunkCase.pcm(): ShortArray = samples?.let { list -> ShortArray(list.size) { list[it].toShort() } }
        ?: generator!!.let { g -> ShortArray(g.count) { (it.toLong() * g.multiplier).toInt().toShort() } }

    private fun sha256Hex(text: String): String =
        MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.US_ASCII)).toFixtureHex()

    /** What the uploader would send, described the way the fixture pins it. */
    private fun describe(buffer: PcmBuffer, chunkSamples: Int, final: Boolean, hashed: Boolean): List<Chunk> =
        (0 until buffer.sealedChunks(chunkSamples, final)).map { seq ->
            val b64 = buffer.payload(seq, chunkSamples)
            val bytes = Base64.getDecoder().decode(b64)
            if (hashed) {
                Chunk(seq, byte_length = bytes.size, b64_length = b64.length, b64_sha256 = sha256Hex(b64))
            } else {
                Chunk(seq, bytes_hex = bytes.toFixtureHex(), pcm16_b64 = b64)
            }
        }

    @Test
    fun fixture_hasCasesToRun() {
        assertTrue(fixture.chunk_size.isNotEmpty())
        assertTrue(fixture.chunk_cases.isNotEmpty())
        assertTrue(fixture.float_to_pcm16.cases.isNotEmpty())
    }

    @Test
    fun chunkBytes_matchesEveryRow() {
        for (row in fixture.chunk_size) {
            assertEquals(
                "max_chunk_bytes ${row.max_chunk_bytes}",
                row.chunk_bytes,
                Pcm16.chunkBytes(fixture.sample_rate, row.max_chunk_bytes),
            )
        }
    }

    @Test
    fun pcmBuffer_sealsTheGoldenChunks_whileRecordingAndAfterStop() {
        for (case in fixture.chunk_cases) {
            val pcm = case.pcm()
            val bytes = Pcm16.chunkBytes(fixture.sample_rate, case.max_chunk_bytes)
            assertNotNull(case.name, bytes)
            val chunkSamples = bytes!! / 2
            val buffer = PcmBuffer(pcm.size)
            buffer.append(pcm)
            val hashed = case.generator != null

            assertEquals("${case.name} open", case.open, describe(buffer, chunkSamples, final = false, hashed))
            assertEquals("${case.name} final", case.final, describe(buffer, chunkSamples, final = true, hashed))
        }
    }

    @Test
    fun quantize_followsTheFloatRule() {
        for (case in fixture.float_to_pcm16.cases) {
            val input = FloatArray(case.input.size) { i ->
                val value = case.input[i]
                if (value.isString) value.content.toFloat() else value.double.toFloat()
            }

            assertEquals(case.name, case.expect, Pcm16.quantize(input).map { it.toInt() })
        }
    }
}
