package com.mobaide.app.ui.common.pixel

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.TopAppBarScrollBehavior
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.ui.theme.MobaideTheme

/**
 * Drop-in for M3 [TopAppBar] with the web header treatment: bg-colored bar,
 * Departure Mono title in accent, and the system's 2dp bottom rule (never a
 * hairline).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PixelTopBar(
    title: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    navigationIcon: @Composable () -> Unit = {},
    actions: @Composable RowScope.() -> Unit = {},
    scrollBehavior: TopAppBarScrollBehavior? = null,
) {
    Column(modifier = modifier) {
        TopAppBar(
            title = title,
            navigationIcon = navigationIcon,
            actions = actions,
            colors = TopAppBarDefaults.topAppBarColors(
                containerColor = MaterialTheme.colorScheme.background,
                scrolledContainerColor = MaterialTheme.colorScheme.background,
                titleContentColor = MaterialTheme.colorScheme.primary,
                navigationIconContentColor = MaterialTheme.colorScheme.onBackground,
                actionIconContentColor = MaterialTheme.colorScheme.onBackground,
            ),
            scrollBehavior = scrollBehavior,
        )
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(2.dp)
                .background(MaterialTheme.colorScheme.outlineVariant),
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Preview(name = "Top bar dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun PixelTopBarPreviewDark() {
    MobaideTheme(darkTheme = true) {
        PixelTopBar(title = { Text("Sessions") })
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Preview(name = "Top bar light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun PixelTopBarPreviewLight() {
    MobaideTheme(darkTheme = false) {
        PixelTopBar(title = { Text("Sessions") })
    }
}
