package com.mobaide.app.data.auth

import android.content.Context
import android.content.SharedPreferences
import java.io.File
import java.security.GeneralSecurityException
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Recovery of an encrypted prefs file that cannot be decrypted, and the
 * backup rules that keep such a file from being restored. Robolectric has no
 * AndroidKeyStore, so a fake `create` returns plain prefs over the same file
 * and throws the way a restored, undecryptable keyset does.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class EncryptedPrefsTest {

    private lateinit var context: Context

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        EncryptedPrefsFiles.ALL.forEach { context.deleteSharedPreferences(it) }
    }

    @After
    fun tearDown() {
        EncryptedPrefsFiles.ALL.forEach { context.deleteSharedPreferences(it) }
    }

    private fun failingCreate(failures: Int, error: () -> Exception): (Context, String) -> SharedPreferences {
        var left = failures
        return { ctx, name ->
            if (left-- > 0) throw error()
            ctx.getSharedPreferences(name, Context.MODE_PRIVATE)
        }
    }

    private fun seed(name: String, value: String) {
        context.getSharedPreferences(name, Context.MODE_PRIVATE).edit().putString("k", value).commit()
    }

    @Test
    fun oneFailure_retriesAndKeepsTheData() {
        seed(EncryptedPrefsFiles.AUTH, "token")

        val prefs = openEncryptedPrefs(
            context,
            EncryptedPrefsFiles.AUTH,
            failingCreate(1) { GeneralSecurityException("transient") },
        )

        assertEquals("token", prefs.getString("k", null))
    }

    @Test
    fun twoFailures_deletesOnlyThatFileAndReturnsEmptyPrefs() {
        seed(EncryptedPrefsFiles.CONNECTIONS, "restored")
        seed(EncryptedPrefsFiles.AUTH, "token")

        val prefs = openEncryptedPrefs(
            context,
            EncryptedPrefsFiles.CONNECTIONS,
            failingCreate(2) { GeneralSecurityException("cannot decrypt keyset") },
        )

        assertNull(prefs.getString("k", null))
        assertEquals(
            "token",
            context.getSharedPreferences(EncryptedPrefsFiles.AUTH, Context.MODE_PRIVATE).getString("k", null),
        )
    }

    @Test
    fun unrelatedFailure_rethrowsAndKeepsTheData() {
        seed(EncryptedPrefsFiles.USER_IDENTITY, "user")

        try {
            openEncryptedPrefs(
                context,
                EncryptedPrefsFiles.USER_IDENTITY,
                failingCreate(1) { IllegalStateException("bug") },
            )
            fail("expected IllegalStateException")
        } catch (_: IllegalStateException) {
        }

        assertEquals(
            "user",
            context.getSharedPreferences(EncryptedPrefsFiles.USER_IDENTITY, Context.MODE_PRIVATE).getString("k", null),
        )
    }

    @Test
    fun everyEncryptedPrefsFile_isExcludedFromAllBackupSections() {
        val legacy = File("src/main/res/xml/backup_rules.xml").readText()
        val modern = File("src/main/res/xml/data_extraction_rules.xml").readText()

        EncryptedPrefsFiles.ALL.forEach { name ->
            val expected = "<exclude domain=\"sharedpref\" path=\"$name.xml\" />"
            assertTrue("backup_rules.xml must exclude $name", legacy.contains(expected))
            // Once under <cloud-backup>, once under <device-transfer>.
            assertEquals("data_extraction_rules.xml must exclude $name twice", 2, modern.split(expected).size - 1)
        }
    }
}
