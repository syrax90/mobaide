package com.mobaide.app.data.analytics

import android.content.Context
import java.io.File
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * The per-install conversion state against a real DataStore file: outcomes
 * are per account, the armed timestamp is write-once, and the file is listed
 * in both backup-rule files so a restore never carries it onto another
 * install.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ConversionStateStoreTest {

    private lateinit var context: Context
    private lateinit var store: ConversionStateStore

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        clearDataStoreFile()
        store = ConversionStateStore(context)
    }

    @After
    fun tearDown() {
        clearDataStoreFile()
    }

    private fun clearDataStoreFile() {
        File(context.filesDir, "datastore").listFiles()
            ?.filter { it.name.startsWith(ConversionStateStore.DATASTORE_NAME) }
            ?.forEach { it.delete() }
    }

    // Keys are unique per test: the DataStore instance behind the file-scope
    // delegate outlives the per-test file deletion, so state written by one
    // test is still visible to the next.

    @Test
    fun outcome_isKeyedPerAccount() = runTest {
        assertNull(store.getOutcome("keyed-u1"))

        store.setOutcome("keyed-u1", "logged")

        assertEquals("logged", store.getOutcome("keyed-u1"))
        assertNull(store.getOutcome("keyed-u2"))
    }

    @Test
    fun armIfUnset_keepsTheFirstValue() = runTest {
        val first = store.armIfUnset(1_000L)

        assertEquals(first, store.armIfUnset(2_000L))
        assertEquals(first, store.getArmedAt())
    }

    @Test
    fun dataStoreFile_isExcludedFromBackupAndDeviceTransfer() {
        val expected = "path=\"datastore/${ConversionStateStore.DATASTORE_FILE_NAME}\""
        val legacy = File("src/main/res/xml/backup_rules.xml").readText()
        val modern = File("src/main/res/xml/data_extraction_rules.xml").readText()

        assertTrue("backup_rules.xml must exclude the conversion DataStore", legacy.contains(expected))
        // Once under <cloud-backup>, once under <device-transfer>.
        assertEquals(2, modern.split(expected).size - 1)
    }
}
