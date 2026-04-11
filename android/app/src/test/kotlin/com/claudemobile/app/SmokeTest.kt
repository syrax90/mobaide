package com.claudemobile.app

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Minimal bootstrap test to prove that the JVM unit-test source set compiles and
 * runs. Must remain green — anything more interesting belongs in a scoped test class.
 */
class SmokeTest {

    @Test
    fun smokeTest() {
        assertEquals(2, 1 + 1)
    }
}
