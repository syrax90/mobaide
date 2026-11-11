package com.mobaide.app.data.auth

import android.content.Context
import java.io.File
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Unit tests for [DeviceIdentityStore].
 *
 * The production store binds a [androidx.datastore.core.DataStore] to a
 * file under `<context.filesDir>/datastore/device_identity.preferences_pb`
 * via the file-scope `preferencesDataStore(...)` delegate. The delegate
 * caches one DataStore per Context, so two [DeviceIdentityStore]
 * instances constructed with the same Robolectric ApplicationContext
 * share the same backing file — which is exactly the production
 * lifecycle (the singleton-via-Hilt instance survives ViewModel re-
 * creation; the file survives process restart).
 *
 * Robolectric SDK is pinned to 33 to match the rest of the JVM-Compose
 * test suite (see `AgentListScreenTest`).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class DeviceIdentityStoreTest {

    private lateinit var context: Context

    @Before
    fun setUp() {
        // `RuntimeEnvironment.getApplication()` is the Robolectric-native
        // way to obtain the per-JVM Application context — avoids a hard
        // dependency on `androidx.test:core` (`ApplicationProvider`),
        // which is not declared in the project's `libs.versions.toml`.
        context = RuntimeEnvironment.getApplication()
        // Robolectric reuses the application's filesDir across `@Test`
        // methods within a single class run, so the DataStore file would
        // otherwise leak state from a previous method. Delete the file
        // up-front to guarantee a fresh per-test starting condition.
        clearDataStoreFile()
    }

    @After
    fun tearDown() {
        clearDataStoreFile()
    }

    private fun clearDataStoreFile() {
        val datastoreDir = File(context.filesDir, "datastore")
        if (!datastoreDir.exists()) return
        // The exact filename produced by `preferencesDataStore(name = "device_identity")`
        // is `device_identity.preferences_pb`. We delete it plus any
        // sibling temp / lock files DataStore writes during edit { }.
        datastoreDir.listFiles()
            ?.filter { it.name.startsWith(DeviceIdentityStore.DATASTORE_FILE_NAME) }
            ?.forEach { it.delete() }
    }

    @Test
    fun getOrCreateDeviceId_isIdempotentWithinAProcess() = runTest {
        val store = DeviceIdentityStore(context)

        val first = store.getOrCreateDeviceId()
        val second = store.getOrCreateDeviceId()
        val third = store.getOrCreateDeviceId()

        assertEquals(first, second)
        assertEquals(second, third)
    }

    @Test
    fun newInstance_overSameDataStoreFile_returnsPreviouslyPersistedValue() = runTest {
        // Mimics process restart: a brand-new DeviceIdentityStore over the
        // same DataStore file must return the UUID a previous instance
        // wrote. This is the load-bearing property — without it every
        // process restart would mint a new device, defeating the whole
        // user-scoped device tracking story.
        val original = DeviceIdentityStore(context).getOrCreateDeviceId()

        val reinstantiated = DeviceIdentityStore(context).getOrCreateDeviceId()

        assertEquals(original, reinstantiated)
    }
}
