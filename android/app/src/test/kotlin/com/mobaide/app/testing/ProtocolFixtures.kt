package com.mobaide.app.testing

import java.io.File
import kotlinx.serialization.json.Json

/**
 * The root is found by walking up from the Gradle test working directory rather
 * than by a fixed `../../` — the working directory differs between a Gradle run
 * from the repo root, a run from `android/`, and an IDE run configuration.
 */
object ProtocolFixtures {

    val json: Json = Json { ignoreUnknownKeys = true }

    val root: File by lazy {
        var candidate: File? = File(System.getProperty("user.dir") ?: ".").absoluteFile
        while (candidate != null) {
            val fixtures = File(candidate, "tests/protocol_fixtures")
            if (fixtures.isDirectory) return@lazy fixtures
            candidate = candidate.parentFile
        }
        error(
            "tests/protocol_fixtures not found above ${System.getProperty("user.dir")} — " +
                "the golden fixtures live at the repo root",
        )
    }

    /** Read a fixture by its `/`-separated path relative to `tests/protocol_fixtures/`. */
    inline fun <reified T> load(relativePath: String): T {
        val file = File(root, relativePath)
        check(file.isFile) { "fixture not found: $relativePath" }
        return json.decodeFromString<T>(file.readText())
    }
}

/** Lowercase hex, matching the `*_hex` convention every fixture file uses. */
fun ByteArray.toFixtureHex(): String = joinToString("") { "%02x".format(it.toInt() and 0xFF) }
