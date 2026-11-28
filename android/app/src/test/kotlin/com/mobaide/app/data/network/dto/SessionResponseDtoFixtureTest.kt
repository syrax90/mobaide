package com.mobaide.app.data.network.dto

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of `tests/protocol_fixtures/dto/agent/SessionResponse.json`
 * case `chat_attention_request`: the request a notification's Allow and Deny
 * answer reads the same from the agent's wire (nulls included) and from the
 * canonical shape, under the `RestApi` decoder configuration.
 */
class SessionResponseDtoFixtureTest {

    @Serializable
    private data class Case(
        val name: String,
        val wire: JsonObject,
        val decoded_canonical: JsonObject? = null,
    )

    @Serializable
    private data class Fixture(val type: String, val cases: List<Case>)

    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }

    private val fixture: Fixture = ProtocolFixtures.load("dto/agent/SessionResponse.json")

    @Test
    fun chatAttentionRequest_decodesToAnAnswerableRequest() {
        val case = fixture.cases.first { it.name == "chat_attention_request" }

        for (body in listOfNotNull(case.wire, case.decoded_canonical)) {
            val session = json.decodeFromJsonElement(SessionResponse.serializer(), body)
            assertEquals(
                AttentionRequest(
                    id = "r:3:a1b2c3",
                    title = "npm test",
                    tool_kind = "execute",
                    choices = listOf(ChatRespondRequest.ALLOW, ChatRespondRequest.DENY),
                ),
                session.attention_request,
            )
            assertTrue(session.attention_request!!.isAnswerable)
        }
    }

    @Test
    fun malformedAttentionRequest_keepsTheRowWithoutActions() {
        val wire = fixture.cases.first { it.name == "chat_attention_request" }.wire
        val malformed: List<JsonElement> = listOf(
            JsonPrimitive("r:3:a1b2c3"),
            buildJsonObject { put("title", "npm test") },
            buildJsonObject {
                put("id", "r:3:a1b2c3")
                put("choices", "allow_once")
            },
        )

        for (value in malformed) {
            val body = JsonObject(wire + ("attention_request" to value))
            val session = json.decodeFromJsonElement(SessionResponse.serializer(), body)
            assertEquals("s9", session.session_id)
            assertEquals("awaiting_permission", session.attention)
            assertNull(value.toString(), session.attention_request)
        }
    }
}
