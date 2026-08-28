package com.mobaide.app.ui.common.pixel

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import com.mobaide.app.ui.theme.MobaideTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Guards the sign-in brand against the adaptive-icon trap: `imageResource`
 * throws on an XML drawable, and on API 26+ every `mipmap/ic_launcher`
 * resolves to one. Composing the mark on both sides of that SDK line proves
 * the resource it loads is a real raster.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [26, 33], qualifiers = "w360dp-h800dp-port-xxhdpi")
class PixelBrandMarkTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun brandMark_rendersFromRasterResource() {
        composeRule.setContent {
            MobaideTheme(darkTheme = true) { PixelBrandMark() }
        }

        composeRule.onNodeWithContentDescription("Mobaide").assertIsDisplayed()
    }
}
