package com.mobaide.app.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

private val Context.dataStore by preferencesDataStore(name = "mobaide_prefs")

class PreferencesStore(private val context: Context) {

    private val keyLastProject = stringPreferencesKey("last_used_project_name")
    private val keyLastSession = stringPreferencesKey("last_used_session_id")
    private val keyTerminalDisplayMode = stringPreferencesKey("terminal_display_mode")
    private val keyTerminalStaticColumns = intPreferencesKey("terminal_static_columns")

    // Lenient (ignore unknown keys) so adding fields server-side later doesn't
    // crash a stale cached blob.
    private val cacheJson = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val labelListSerializer = ListSerializer(LinearLabelDto.serializer())
    private val stateListSerializer = ListSerializer(LinearStateDto.serializer())

    private fun keyLinearLabels(projectName: String) =
        stringPreferencesKey("linear_labels::$projectName")

    private fun keyLinearStates(projectName: String) =
        stringPreferencesKey("linear_states::$projectName")

    private fun keyRecentCustomPaths(agentKey: String) =
        stringPreferencesKey("recent_custom_paths::$agentKey")

    private val stringListSerializer = ListSerializer(String.serializer())

    val lastUsedProjectName: Flow<String?> =
        context.dataStore.data.map { it[keyLastProject] }
    val lastUsedSessionId: Flow<String?> =
        context.dataStore.data.map { it[keyLastSession] }

    val terminalDisplayMode: Flow<TerminalDisplayMode> =
        context.dataStore.data.map { prefs ->
            prefs[keyTerminalDisplayMode]?.let { stored ->
                runCatching { TerminalDisplayMode.valueOf(stored) }.getOrNull()
            } ?: TerminalDisplayMode.Static
        }

    val terminalStaticColumns: Flow<Int> =
        context.dataStore.data.map { prefs ->
            (prefs[keyTerminalStaticColumns] ?: TERMINAL_STATIC_COLUMNS_DEFAULT)
                .coerceIn(TERMINAL_STATIC_COLUMNS_MIN, TERMINAL_STATIC_COLUMNS_MAX)
        }

    suspend fun getLastUsedProjectName(): String? =
        context.dataStore.data.map { it[keyLastProject] }.first()

    suspend fun getLastUsedSessionId(): String? =
        context.dataStore.data.map { it[keyLastSession] }.first()

    suspend fun setLastUsedProjectName(name: String?) {
        context.dataStore.edit { prefs ->
            if (name == null) prefs.remove(keyLastProject) else prefs[keyLastProject] = name
        }
    }

    suspend fun setLastUsedSessionId(id: String?) {
        context.dataStore.edit { prefs ->
            if (id == null) prefs.remove(keyLastSession) else prefs[keyLastSession] = id
        }
    }

    suspend fun setTerminalDisplayMode(mode: TerminalDisplayMode) {
        context.dataStore.edit { prefs ->
            prefs[keyTerminalDisplayMode] = mode.name
        }
    }

    suspend fun setTerminalStaticColumns(columns: Int) {
        val clamped = columns.coerceIn(TERMINAL_STATIC_COLUMNS_MIN, TERMINAL_STATIC_COLUMNS_MAX)
        context.dataStore.edit { prefs ->
            prefs[keyTerminalStaticColumns] = clamped
        }
    }

    suspend fun getCachedLinearLabels(projectName: String): List<LinearLabelDto> {
        val raw = context.dataStore.data.map { it[keyLinearLabels(projectName)] }.first()
            ?: return emptyList()
        return runCatching {
            cacheJson.decodeFromString(labelListSerializer, raw)
        }.getOrDefault(emptyList())
    }

    suspend fun getCachedLinearStates(projectName: String): List<LinearStateDto> {
        val raw = context.dataStore.data.map { it[keyLinearStates(projectName)] }.first()
            ?: return emptyList()
        return runCatching {
            cacheJson.decodeFromString(stateListSerializer, raw)
        }.getOrDefault(emptyList())
    }

    suspend fun setCachedLinearLabels(projectName: String, labels: List<LinearLabelDto>) {
        val payload = cacheJson.encodeToString(labelListSerializer, labels)
        context.dataStore.edit { prefs ->
            prefs[keyLinearLabels(projectName)] = payload
        }
    }

    suspend fun setCachedLinearStates(projectName: String, states: List<LinearStateDto>) {
        val payload = cacheJson.encodeToString(stateListSerializer, states)
        context.dataStore.edit { prefs ->
            prefs[keyLinearStates(projectName)] = payload
        }
    }

    suspend fun getRecentCustomPaths(agentKey: String): List<String> {
        val raw = context.dataStore.data.map { it[keyRecentCustomPaths(agentKey)] }.first()
            ?: return emptyList()
        return runCatching {
            cacheJson.decodeFromString(stringListSerializer, raw)
        }.getOrDefault(emptyList())
    }

    suspend fun addRecentCustomPath(agentKey: String, path: String) {
        if (path.isBlank()) return
        context.dataStore.edit { prefs ->
            val existing = prefs[keyRecentCustomPaths(agentKey)]?.let { raw ->
                runCatching {
                    cacheJson.decodeFromString(stringListSerializer, raw)
                }.getOrDefault(emptyList())
            } ?: emptyList()
            val merged = (listOf(path) + existing.filter { it != path }).take(MAX_RECENT_CUSTOM_PATHS)
            prefs[keyRecentCustomPaths(agentKey)] = cacheJson.encodeToString(stringListSerializer, merged)
        }
    }

    companion object {
        const val MAX_RECENT_CUSTOM_PATHS: Int = 5
    }
}
