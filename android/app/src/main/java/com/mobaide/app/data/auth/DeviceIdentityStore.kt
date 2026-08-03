package com.mobaide.app.data.auth

import android.content.Context
import android.os.Build
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import java.util.UUID
import kotlinx.coroutines.flow.first

/**
 * Per-install device identity. The first call generates a fresh UUID v4,
 * persists it to a dedicated Preferences DataStore, and returns it; every
 * subsequent call returns the same value across process restarts.
 *
 * The DataStore file (`files/datastore/device_identity.preferences_pb`) is
 * excluded from Android Auto Backup and device-to-device transfer (see
 * `res/xml/data_extraction_rules.xml`) so a restored backup does not
 * duplicate the device id onto two physical devices — the SOTA pitfall
 * flagged in the Signal / Bitwarden device-identity literature.
 *
 * Survives sign-out: the device's identity is independent of the user
 * session. Only `clear-data` / uninstall resets it.
 */
class DeviceIdentityStore(context: Context) {

    private val dataStore: DataStore<Preferences> = context.deviceIdentityDataStore

    suspend fun getOrCreateDeviceId(): String {
        val existing = dataStore.data.first()[KEY_DEVICE_UUID]
        if (existing != null) return existing
        // edit { } serialises concurrent writes; the lambda reads the latest
        // snapshot first to avoid clobbering a UUID written by a parallel
        // call that won the race to commit.
        var written: String = ""
        dataStore.edit { prefs ->
            val racedValue = prefs[KEY_DEVICE_UUID]
            if (racedValue != null) {
                written = racedValue
            } else {
                val fresh = UUID.randomUUID().toString()
                prefs[KEY_DEVICE_UUID] = fresh
                written = fresh
            }
        }
        return written
    }

    fun getDeviceName(): String {
        val manufacturer = Build.MANUFACTURER?.trim().orEmpty()
        val model = Build.MODEL?.trim().orEmpty()
        return when {
            manufacturer.isEmpty() && model.isEmpty() -> "Android device"
            model.startsWith(manufacturer, ignoreCase = true) -> model
            manufacturer.isEmpty() -> model
            model.isEmpty() -> manufacturer
            else -> "$manufacturer $model"
        }
    }

    companion object {
        const val DATASTORE_FILE_NAME = "device_identity"

        /**
         * Reported when registering this device, so the devices screen can
         * label it. iOS sends "ios" and the web client "web".
         */
        const val DEVICE_PLATFORM = "android"

        private val KEY_DEVICE_UUID = stringPreferencesKey("device_uuid")
    }
}

private val Context.deviceIdentityDataStore: DataStore<Preferences> by preferencesDataStore(
    name = DeviceIdentityStore.DATASTORE_FILE_NAME,
)
