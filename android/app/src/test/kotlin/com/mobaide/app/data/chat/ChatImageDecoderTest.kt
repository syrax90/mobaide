package com.mobaide.app.data.chat

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Unit tests for [ChatImageDecoder.sampleSize] and [ChatImageDecoder.fitSampleSize]
 * — the power-of-two subsampling that keeps a decoded agent image within the
 * display bound and no larger than the box it is drawn in.
 */
class ChatImageDecoderTest {

    @Test
    fun sampleSize_isOneWhenTheImageAlreadyFits() {
        assertEquals(1, ChatImageDecoder.sampleSize(2048, 2048, 2048))
        assertEquals(1, ChatImageDecoder.sampleSize(10, 300, 2048))
    }

    @Test
    fun sampleSize_followsTheLongestSide() {
        assertEquals(2, ChatImageDecoder.sampleSize(4096, 100, 2048))
        assertEquals(2, ChatImageDecoder.sampleSize(100, 4096, 2048))
        assertEquals(8, ChatImageDecoder.sampleSize(10_000, 300, 2048))
    }

    @Test
    fun sampleSize_countsAPartlySampledPixelAsAWholeOne() {
        // 4097 px at 1/2 is 2049 px, one over the bound.
        assertEquals(4, ChatImageDecoder.sampleSize(4097, 1, 2048))
        assertEquals(2, ChatImageDecoder.sampleSize(2049, 1, 2048))
    }

    @Test
    fun fitSampleSize_isOneWhenTheImageIsDrawnAtFullSize() {
        assertEquals(1, ChatImageDecoder.fitSampleSize(500, 300, 1000, 1000))
        assertEquals(1, ChatImageDecoder.fitSampleSize(1000, 1000, 1000, 1000))
        // Drawn at 1000 px: 1/2 would leave 999.5 px.
        assertEquals(1, ChatImageDecoder.fitSampleSize(1999, 1999, 1000, 1000))
    }

    @Test
    fun fitSampleSize_followsTheSideThatLimitsTheFit() {
        // 4000 x 1000 fitted into 1000 x 1000 is drawn at 1000 x 250.
        assertEquals(4, ChatImageDecoder.fitSampleSize(4000, 1000, 1000, 1000))
        assertEquals(4, ChatImageDecoder.fitSampleSize(1000, 4000, 1000, 1000))
        assertEquals(2, ChatImageDecoder.fitSampleSize(2400, 900, 1080, 900))
    }

    @Test
    fun fitSampleSize_neverSamplesBelowTheDrawnSize() {
        // Drawn at 1000 px: 1/2 leaves 1500 px, 1/4 would leave 750.
        assertEquals(2, ChatImageDecoder.fitSampleSize(3000, 3000, 1000, 1000))
        assertEquals(2, ChatImageDecoder.fitSampleSize(2000, 2000, 1000, 1000))
        assertEquals(4, ChatImageDecoder.fitSampleSize(4000, 4000, 1000, 1000))
    }

    @Test
    fun fitSampleSize_withoutABoxDoesNotSample() {
        assertEquals(1, ChatImageDecoder.fitSampleSize(4000, 4000, 0, 1000))
        assertEquals(1, ChatImageDecoder.fitSampleSize(4000, 4000, 1000, -1))
    }

    @Test
    fun fitSampleSize_aHugeBoxSideDoesNotOverflow() {
        assertEquals(4, ChatImageDecoder.fitSampleSize(4000, 1000, 1000, Int.MAX_VALUE))
        assertEquals(1, ChatImageDecoder.fitSampleSize(Int.MAX_VALUE, 1, Int.MAX_VALUE, Int.MAX_VALUE))
    }
}
