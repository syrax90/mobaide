package com.claudemobile.app.data.local

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "claude_mobile_prefs")

/**
 * Lightweight Preferences DataStore for the few fields the Android client wants to remember
 * across launches: the most recently used project name (for pre-selecting the picker) and the
 * most recently created session id (for auto-resume on app restart). See CLA-14.
 */
class PreferencesStore(private val context: Context) {

    private val keyLastProject = stringPreferencesKey("last_used_project_name")
    private val keyLastSession = stringPreferencesKey("last_used_session_id")

    val lastUsedProjectName: Flow<String?> =
        context.dataStore.data.map { it[keyLastProject] }
    val lastUsedSessionId: Flow<String?> =
        context.dataStore.data.map { it[keyLastSession] }

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
}
