package com.mobaide.app.data.local

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto
import com.mobaide.app.data.review.ReviewPromptState
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.io.IOException
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json

private val Context.dataStore by preferencesDataStore(name = "mobaide_prefs")

class PreferencesStore(private val context: Context) {

    private val keyLastProject = stringPreferencesKey("last_used_project_name")
    private val keyLastSession = stringPreferencesKey("last_used_session_id")
    private val keyLastClaudeOptions = stringPreferencesKey("last_used_claude_options")
    private val keyLastCodexOptions = stringPreferencesKey("last_used_codex_options")
    private val keyLastAntigravityOptions = stringPreferencesKey("last_used_antigravity_options")
    private val keyLastCopilotOptions = stringPreferencesKey("last_used_copilot_options")
    private val keyLastAutoScanOptions = stringPreferencesKey("last_used_auto_scan_options")
    private val keyTerminalDisplayMode = stringPreferencesKey("terminal_display_mode")
    private val keyThemeMode = stringPreferencesKey("theme_mode")
    private val keyTerminalStaticColumns = intPreferencesKey("terminal_static_columns")
    private val keyVoiceCleanupNoticeSeen = booleanPreferencesKey("voice_cleanup_notice_seen")
    private val keyChatFocusView = booleanPreferencesKey("chat_focus_view")

    // Device-local, not account-scoped (device backups carry them): no sign-out
    // sweep touches them, since the store's quota follows the device's store
    // account, not the Mobaide one.
    private val keyReviewLaunchCount = intPreferencesKey("review_prompt_launch_count")
    private val keyReviewGoodSessions = intPreferencesKey("review_prompt_good_sessions")
    private val keyReviewLastRequestedAt = longPreferencesKey("review_prompt_last_requested_at")

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
        stringPreferencesKey("$RECENT_CUSTOM_PATHS_PREFIX$agentKey")

    private fun keyVoiceMicHidden(agentKey: String) =
        booleanPreferencesKey("$VOICE_MIC_HIDDEN_PREFIX$agentKey")

    private fun keyRecentShortcutChords(sessionKindKey: String) =
        stringPreferencesKey("recent_shortcut_chords::$sessionKindKey")

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

    val themeMode: Flow<ThemeMode> =
        context.dataStore.data
            // Unlike the screen-scoped preference flows, this one gates
            // MainActivity's first frame — a corrupt or unreadable prefs file
            // must degrade to the Auto default, never break the whole UI.
            .catch { e ->
                if (e is IOException) emit(emptyPreferences()) else throw e
            }
            .map { prefs ->
                prefs[keyThemeMode]?.let { stored ->
                    runCatching { ThemeMode.valueOf(stored) }.getOrNull()
                } ?: ThemeMode.Auto
            }

    /** Whether the user hid the session mic for this agent (undone in Settings → Voice input). */
    fun voiceMicHidden(agentKey: String): Flow<Boolean> =
        context.dataStore.data.map { it[keyVoiceMicHidden(agentKey)] ?: false }

    suspend fun setVoiceMicHidden(agentKey: String, hidden: Boolean) {
        context.dataStore.edit { prefs ->
            if (hidden) prefs[keyVoiceMicHidden(agentKey)] = true
            else prefs.remove(keyVoiceMicHidden(agentKey))
        }
    }

    /** The user pressed "Got it" on the notice shown before the first cleanup opt-in. */
    val voiceCleanupNoticeSeen: Flow<Boolean> =
        context.dataStore.data.map { it[keyVoiceCleanupNoticeSeen] ?: false }

    suspend fun setVoiceCleanupNoticeSeen() {
        context.dataStore.edit { prefs -> prefs[keyVoiceCleanupNoticeSeen] = true }
    }

    /**
     * Chat focus view: answers and questions only, thinking and tool calls
     * folded into one row per run. On by default; one setting for every chat.
     */
    val chatFocusView: Flow<Boolean> =
        context.dataStore.data
            .catch { e -> if (e is IOException) emit(emptyPreferences()) else throw e }
            .map { it[keyChatFocusView] ?: true }

    suspend fun setChatFocusView(on: Boolean) {
        context.dataStore.edit { prefs -> prefs[keyChatFocusView] = on }
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

    suspend fun getLastUsedClaudeOptions(): ClaudeLaunchPrefs? {
        val raw = context.dataStore.data.map { it[keyLastClaudeOptions] }.first()
            ?: return null
        return runCatching {
            cacheJson.decodeFromString(ClaudeLaunchPrefs.serializer(), raw)
        }.getOrNull()
    }

    suspend fun setLastUsedClaudeOptions(prefs: ClaudeLaunchPrefs) {
        val payload = cacheJson.encodeToString(ClaudeLaunchPrefs.serializer(), prefs)
        context.dataStore.edit { it[keyLastClaudeOptions] = payload }
    }

    // Auto Scan reuses the ClaudeLaunchPrefs shape but persists under its own
    // key, so scan choices never pre-fill the Claude Code sheet (or vice versa).
    suspend fun getLastUsedAutoScanOptions(): ClaudeLaunchPrefs? {
        val raw = context.dataStore.data.map { it[keyLastAutoScanOptions] }.first()
            ?: return null
        return runCatching {
            cacheJson.decodeFromString(ClaudeLaunchPrefs.serializer(), raw)
        }.getOrNull()
    }

    suspend fun setLastUsedAutoScanOptions(prefs: ClaudeLaunchPrefs) {
        val payload = cacheJson.encodeToString(ClaudeLaunchPrefs.serializer(), prefs)
        context.dataStore.edit { it[keyLastAutoScanOptions] = payload }
    }

    suspend fun getLastUsedCodexOptions(): CodexLaunchPrefs? {
        val raw = context.dataStore.data.map { it[keyLastCodexOptions] }.first()
            ?: return null
        return runCatching {
            cacheJson.decodeFromString(CodexLaunchPrefs.serializer(), raw)
        }.getOrNull()
    }

    suspend fun setLastUsedCodexOptions(prefs: CodexLaunchPrefs) {
        val payload = cacheJson.encodeToString(CodexLaunchPrefs.serializer(), prefs)
        context.dataStore.edit { it[keyLastCodexOptions] = payload }
    }

    suspend fun getLastUsedAntigravityOptions(): AntigravityLaunchPrefs? {
        val raw = context.dataStore.data.map { it[keyLastAntigravityOptions] }.first()
            ?: return null
        return runCatching {
            cacheJson.decodeFromString(AntigravityLaunchPrefs.serializer(), raw)
        }.getOrNull()
    }

    suspend fun setLastUsedAntigravityOptions(prefs: AntigravityLaunchPrefs) {
        val payload = cacheJson.encodeToString(AntigravityLaunchPrefs.serializer(), prefs)
        context.dataStore.edit { it[keyLastAntigravityOptions] = payload }
    }

    suspend fun getLastUsedCopilotOptions(): CopilotLaunchPrefs? {
        val raw = context.dataStore.data.map { it[keyLastCopilotOptions] }.first()
            ?: return null
        return runCatching {
            cacheJson.decodeFromString(CopilotLaunchPrefs.serializer(), raw)
        }.getOrNull()
    }

    suspend fun setLastUsedCopilotOptions(prefs: CopilotLaunchPrefs) {
        val payload = cacheJson.encodeToString(CopilotLaunchPrefs.serializer(), prefs)
        context.dataStore.edit { it[keyLastCopilotOptions] = payload }
    }

    suspend fun setThemeMode(mode: ThemeMode) {
        context.dataStore.edit { prefs ->
            prefs[keyThemeMode] = mode.name
        }
    }

    suspend fun setTerminalDisplayMode(mode: TerminalDisplayMode) {
        context.dataStore.edit { prefs ->
            prefs[keyTerminalDisplayMode] = mode.name
        }
    }

    suspend fun incrementReviewLaunchCount() {
        context.dataStore.edit { prefs ->
            prefs[keyReviewLaunchCount] = (prefs[keyReviewLaunchCount] ?: 0) + 1
        }
    }

    /** Counts one more good session and returns the counters as written. */
    suspend fun incrementReviewGoodSessions(): ReviewPromptState {
        val prefs = context.dataStore.edit { prefs ->
            prefs[keyReviewGoodSessions] = (prefs[keyReviewGoodSessions] ?: 0) + 1
        }
        return ReviewPromptState(
            launchCount = prefs[keyReviewLaunchCount] ?: 0,
            goodSessions = prefs[keyReviewGoodSessions] ?: 0,
            lastRequestedAtMs = prefs[keyReviewLastRequestedAt],
        )
    }

    /** Starts the cooldown and the next run of good sessions from zero. */
    suspend fun markReviewRequested(nowMillis: Long) {
        context.dataStore.edit { prefs ->
            prefs[keyReviewLastRequestedAt] = nowMillis
            prefs[keyReviewGoodSessions] = 0
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
        val trimmed = path.trim()
        if (trimmed.isEmpty()) return
        context.dataStore.edit { prefs ->
            val existing = prefs[keyRecentCustomPaths(agentKey)]?.let { raw ->
                runCatching {
                    cacheJson.decodeFromString(stringListSerializer, raw)
                }.getOrDefault(emptyList())
            } ?: emptyList()
            val merged =
                (listOf(trimmed) + existing.filter { it != trimmed }).take(MAX_RECENT_CUSTOM_PATHS)
            prefs[keyRecentCustomPaths(agentKey)] = cacheJson.encodeToString(stringListSerializer, merged)
        }
    }

    /**
     * Drops [path] from the agent's bucket. Matches the trimmed form (the key
     * [addRecentCustomPath] dedupes on) and the raw argument, so entries stored
     * untrimmed before that trim existed are still removable — the chip hands
     * back whatever string it rendered.
     */
    suspend fun removeRecentCustomPath(agentKey: String, path: String) {
        val trimmed = path.trim()
        context.dataStore.edit { prefs ->
            val key = keyRecentCustomPaths(agentKey)
            val existing = prefs[key]?.let { raw ->
                runCatching {
                    cacheJson.decodeFromString(stringListSerializer, raw)
                }.getOrDefault(emptyList())
            } ?: return@edit
            val remaining = existing.filter { it != path && it != trimmed }
            // Drop the key outright once emptied rather than parking an "[]" blob
            // under an agent URL that may never come back (mirrors iOS, and the
            // remove-don't-blank precedent of setLastUsedProjectName above).
            if (remaining.isEmpty()) prefs.remove(key)
            else prefs[key] = cacheJson.encodeToString(stringListSerializer, remaining)
        }
    }

    /**
     * Drops every agent's recent-directory bucket at sign-out. An agent switch keeps them (see
     * [com.mobaide.app.data.auth.SessionManager.agentDisconnect]). Swept by prefix rather than
     * by the current agent key, which sign-out clears from the token store concurrently with
     * this write, and which would in any case strand every previously-connected agent's bucket.
     */
    suspend fun removeAllRecentCustomPaths() {
        context.dataStore.edit { prefs ->
            prefs.asMap().keys
                .filter { it.name.startsWith(RECENT_CUSTOM_PATHS_PREFIX) }
                .forEach { prefs -= it }
        }
    }

    /**
     * Most-recently-used shortcut chords for one session kind, newest first.
     * Entries are opaque chord tokens (see KeyChord.token()); unparseable entries
     * are the caller's concern, keeping this store free of UI types.
     */
    suspend fun getRecentShortcutChords(sessionKindKey: String): List<String> {
        val raw = context.dataStore.data.map { it[keyRecentShortcutChords(sessionKindKey)] }.first()
            ?: return emptyList()
        return runCatching {
            cacheJson.decodeFromString(stringListSerializer, raw)
        }.getOrDefault(emptyList())
    }

    suspend fun addRecentShortcutChord(sessionKindKey: String, chordToken: String) {
        if (chordToken.isBlank()) return
        context.dataStore.edit { prefs ->
            val existing = prefs[keyRecentShortcutChords(sessionKindKey)]?.let { raw ->
                runCatching {
                    cacheJson.decodeFromString(stringListSerializer, raw)
                }.getOrDefault(emptyList())
            } ?: emptyList()
            val merged = (listOf(chordToken) + existing.filter { it != chordToken })
                .take(MAX_RECENT_SHORTCUT_CHORDS)
            prefs[keyRecentShortcutChords(sessionKindKey)] =
                cacheJson.encodeToString(stringListSerializer, merged)
        }
    }

    companion object {
        const val RECENT_CUSTOM_PATHS_PREFIX: String = "recent_custom_paths::"
        const val VOICE_MIC_HIDDEN_PREFIX: String = "voice_mic_hidden::"
        const val MAX_RECENT_CUSTOM_PATHS: Int = 5
        const val MAX_RECENT_SHORTCUT_CHORDS: Int = 10
    }
}
