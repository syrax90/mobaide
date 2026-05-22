package com.mobaide.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins the [BuildConfig.PRIVACY_POLICY_URL] default to the canonical URL
 * declared in `central_server/legal/privacy.md` §13 and surfaced from
 * `LoginScreen` / `RegisterScreen` / `SettingsScreen` via Custom Tabs.
 *
 * The value is resolved in `android/app/build.gradle.kts` from
 * `PRIVACY_POLICY_URL` env / `local.properties` / a hard-coded default;
 * any debug build performed without those overrides ships the default.
 * If a future refactor accidentally drops the default branch (e.g. by
 * making the field nullable), this guard fails before the change can
 * regress the in-app links.
 */
class PrivacyPolicyBuildConfigTest {

    @Test
    fun privacyPolicyUrl_field_isPresentAndNonEmpty() {
        val url: String? = BuildConfig.PRIVACY_POLICY_URL
        assertNotNull("BuildConfig.PRIVACY_POLICY_URL must be defined", url)
        assertTrue(
            "BuildConfig.PRIVACY_POLICY_URL must not be blank",
            url!!.isNotBlank(),
        )
    }

    @Test
    fun privacyPolicyUrl_default_matchesCanonicalSelfHostedUrl() {
        val expected = "https://mobaide.com/privacy.html"
        val actual = BuildConfig.PRIVACY_POLICY_URL
        // CI / release pipelines may override via the PRIVACY_POLICY_URL
        // env var; under the standard debug unit-test build (no env var
        // set) the default branch in build.gradle.kts wins.
        val override = System.getenv("PRIVACY_POLICY_URL")
        if (override.isNullOrBlank()) {
            assertEquals(
                "PRIVACY_POLICY_URL default must match the canonical " +
                    "self-hosted URL from privacy.md §13",
                expected,
                actual,
            )
        } else {
            assertEquals(
                "When PRIVACY_POLICY_URL env override is set, " +
                    "BuildConfig must use it verbatim",
                override,
                actual,
            )
        }
    }
}
