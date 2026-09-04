package com.mobaide.app.data.analytics

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

/**
 * Per-install state of the once-per-account `agent_paired` conversion: the
 * arming time and the outcome per account.
 *
 * Its own DataStore file, excluded from Auto Backup and device-to-device
 * transfer (`res/xml/backup_rules.xml`, `res/xml/data_extraction_rules.xml`)
 * like the device identity: an arming time restored from an older install
 * would let a pairing made in between pass the window gate on this one and
 * count as a fresh install conversion. Never swept on sign-out: a
 * sign-out/sign-in cycle must not log again.
 */
class ConversionStateStore(context: Context) {

    private val dataStore: DataStore<Preferences> = context.conversionStateDataStore

    suspend fun getOutcome(userId: String): String? =
        dataStore.data.map { it[keyOutcome(userId)] }.first()

    suspend fun setOutcome(userId: String, outcome: String) {
        dataStore.edit { it[keyOutcome(userId)] = outcome }
    }

    suspend fun getArmedAt(): Long? = dataStore.data.map { it[KEY_ARMED_AT] }.first()

    /** Records [nowMillis] as the conversion window's start once; returns the stored value. */
    suspend fun armIfUnset(nowMillis: Long): Long {
        val prefs = dataStore.edit { if (it[KEY_ARMED_AT] == null) it[KEY_ARMED_AT] = nowMillis }
        return prefs[KEY_ARMED_AT] ?: nowMillis
    }

    private fun keyOutcome(userId: String) = stringPreferencesKey("$OUTCOME_PREFIX$userId")

    companion object {
        const val DATASTORE_NAME: String = "ads_conversion"
        const val DATASTORE_FILE_NAME: String = "$DATASTORE_NAME.preferences_pb"
        const val OUTCOME_PREFIX: String = "agent_paired_logged::"
        private val KEY_ARMED_AT = longPreferencesKey("armed_at")
    }
}

private val Context.conversionStateDataStore: DataStore<Preferences> by preferencesDataStore(
    name = ConversionStateStore.DATASTORE_NAME,
)
