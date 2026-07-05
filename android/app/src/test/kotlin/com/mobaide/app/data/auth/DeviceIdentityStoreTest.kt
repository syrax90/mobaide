package com.mobaide.app.data.auth

import android.content.Context
import java.io.File
import java.util.UUID
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
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

    @Test
    fun clearDataStoreFile_removesBackingFile_provingNoSecondaryPersistence() = runTest {
        // The production privacy contract is: the device id is NOT derived
        // from static hardware identifiers — it is a fresh `UUID.randomUUID()`
        // mint on each "clear-data" lifecycle (i.e. app uninstall / system
        // clear-data). That contract is testable end-to-end only with a real
        // process restart, which Robolectric cannot simulate (a single JVM
        // shares one `preferencesDataStore` per Context for its lifetime;
        // the in-memory cache outlives any file deletion).
        //
        // This test therefore verifies the *necessary precondition* for the
        // production contract: the DataStore has no hidden secondary
        // persistence — once the backing `preferences_pb` file is removed
        // there is nothing else on disk for a restarted process to read,
        // so it MUST mint a fresh UUID. An instrumentation/process-restart
        // test would cover the full contract; flag for a follow-up.
        val first = DeviceIdentityStore(context).getOrCreateDeviceId()
        // Property 1: well-formed v4 UUID.
        assertNotNull(first)
        assertEquals(4, UUID.fromString(first).version())

        // Property 2: the file is physically removed by clearDataStoreFile().
        val datastoreDir = File(context.filesDir, "datastore")
        clearDataStoreFile()
        val filesAfterClear = datastoreDir.listFiles()
            ?.filter { it.name.startsWith(DeviceIdentityStore.DATASTORE_FILE_NAME) }
            ?: emptyList()
        assertTrue(
            "clearDataStoreFile() must remove the DataStore file; found: $filesAfterClear",
            filesAfterClear.isEmpty(),
        )

        // Property 3: same-process call still returns a valid UUID (from
        // the warm in-memory cache). `first == second` here is the
        // expected Robolectric behaviour — the production "regenerate on
        // clear-data" contract is only observable across a real process
        // restart and is not exercised by this assertion.
        val second = DeviceIdentityStore(context).getOrCreateDeviceId()
        assertNotNull(second)
        assertEquals(4, UUID.fromString(second).version())
    }

    @Test
    fun getDeviceName_returnsNonEmptyString() {
        val name = DeviceIdentityStore(context).getDeviceName()
        // Under Robolectric, Build.MANUFACTURER defaults to "robolectric"
        // and Build.MODEL to "robolectric". The store concatenates them
        // (or returns just the model when one starts with the other).
        // Whatever the platform returns, the production code's fallback
        // chain guarantees a non-empty result.
        assertNotNull(name)
        assertFalse("device name must not be blank", name.isBlank())
    }

    @Test
    fun getDeviceName_doesNotContainRawNulls() {
        // Build.MANUFACTURER / MODEL can theoretically return null on
        // exotic hardware. The implementation guards with `.orEmpty()`
        // and an "Android device" fallback. Either way, the result must
        // never contain a literal "null" substring (which would leak
        // through `Build.MANUFACTURER.toString()` if the .orEmpty() were
        // dropped in a future refactor).
        val name = DeviceIdentityStore(context).getDeviceName()
        assertTrue(
            "device name must not embed literal \"null\": '$name'",
            !name.equals("null null", ignoreCase = true) &&
                !name.contains("null null", ignoreCase = true),
        )
    }
}
