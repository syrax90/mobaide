package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of `tests/protocol_fixtures/dto/agent/ChatStoredSessionsResponse.json`:
 * the agent sends every key, nulls included; the canonical shape drops them,
 * and the DTO defaults must read both to the same value under the `RestApi`
 * decoder configuration.
 */
class ChatStoredSessionsDtoFixtureTest {

    @Serializable
    private data class Case(
        val name: String,
        val wire: JsonObject,
        val decoded_canonical: JsonObject,
    )

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private val fixture: Fixture = ProtocolFixtures.load("dto/agent/ChatStoredSessionsResponse.json")

    private fun decode(name: String): ChatStoredSessionsResponse =
        json.decodeFromJsonElement(
            ChatStoredSessionsResponse.serializer(),
            fixture.cases.first { it.name == name }.wire,
        )

    @Test
    fun fixture_hasCasesToRun() {
        assertEquals("ChatStoredSessionsResponse", fixture.type)
        assertTrue(fixture.cases.isNotEmpty())
    }

    @Test
    fun everyCase_decodesFromWireAndCanonicalAlike() {
        for (case in fixture.cases) {
            val fromWire = json.decodeFromJsonElement(ChatStoredSessionsResponse.serializer(), case.wire)
            val fromCanonical =
                json.decodeFromJsonElement(ChatStoredSessionsResponse.serializer(), case.decoded_canonical)
            assertEquals(case.name, fromWire, fromCanonical)
        }
    }

    @Test
    fun pageWithOpenSession_decodesTheOpenRowAndTheCursor() {
        assertEquals(
            ChatStoredSessionsResponse(
                sessions = listOf(
                    ChatStoredSession(
                        session_id = "7d5de896-463a-4ea9-98b4-c24e3e20398d",
                        cwd = "/home/user/proj",
                        title = "Refactor the login flow",
                        updated_at = "2026-11-07T11:58:00.000Z",
                        open_session_id = "a1b2c3d4e5f6",
                    ),
                    ChatStoredSession(
                        session_id = "0f4e2c1a-9b8d-4c7e-a6f5-112233445566",
                        cwd = "/home/user/proj",
                    ),
                ),
                next_cursor = "offset:50",
            ),
            decode("page_with_open_session"),
        )
    }

    @Test
    fun rowOpenInAnotherApp_decodesOpenElsewhere() {
        val rows = decode("row_open_in_another_app").sessions

        assertEquals(listOf(true, false), rows.map { it.open_elsewhere })
        assertEquals(listOf(null, "a1b2c3d4e5f6"), rows.map { it.open_session_id })
    }

    @Test
    fun aRowFromAnAgentWithoutTheField_isNotOpenElsewhere() {
        assertEquals(listOf(false, false), decode("page_with_open_session").sessions.map { it.open_elsewhere })
    }

    @Test
    fun emptyLastPage_hasNoCursor() {
        assertEquals(ChatStoredSessionsResponse(sessions = emptyList(), next_cursor = null), decode("empty_last_page"))
    }
}
