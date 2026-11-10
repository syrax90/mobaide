package com.mobaide.app.ui.chat.markdown

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.mobaide.app.ui.chat.copyWithFeedback
import com.mobaide.app.ui.chat.openExternalLink
import com.mobaide.app.ui.common.pixel.PixelCheckbox
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

val CodeFontFamily: FontFamily = FontFamily.Monospace

@Immutable
private data class MdColors(val text: Color, val muted: Color, val link: Color, val codeBackground: Color)

/** Renders [text] as Markdown; parsing is memoized on the text itself. */
@Composable
fun MarkdownText(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = LocalContentColor.current,
    style: TextStyle = MaterialTheme.typography.bodyMedium,
) {
    val blocks = remember(text) { Markdown.parse(text) }
    MarkdownBlocks(blocks = blocks, modifier = modifier, color = color, style = style)
}

@Composable
fun MarkdownBlocks(
    blocks: List<MdBlock>,
    modifier: Modifier = Modifier,
    color: Color = LocalContentColor.current,
    style: TextStyle = MaterialTheme.typography.bodyMedium,
) {
    val colors = MdColors(
        text = color,
        muted = MaterialTheme.colorScheme.onSurfaceVariant,
        link = MaterialTheme.colorScheme.primary,
        codeBackground = MaterialTheme.colorScheme.surfaceContainerHighest,
    )
    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        blocks.forEach { block -> BlockView(block, colors, style, depth = 0) }
    }
}

@Composable
private fun BlockView(block: MdBlock, colors: MdColors, style: TextStyle, depth: Int) {
    when (block) {
        is MdBlock.Paragraph -> InlineText(block.inlines, colors, style.copy(color = colors.text))
        is MdBlock.Heading -> {
            val size = when (block.level) {
                1 -> 20.sp
                2 -> 18.sp
                else -> 16.sp
            }
            InlineText(
                block.inlines,
                colors,
                style.copy(color = colors.text, fontSize = size, lineHeight = size * 1.35f, fontWeight = FontWeight.SemiBold),
                modifier = Modifier.padding(top = 4.dp),
            )
        }
        is MdBlock.Code -> CodeBlock(block.language, block.code)
        is MdBlock.Quote -> Row(modifier = Modifier.height(IntrinsicSize.Min)) {
            Box(
                modifier = Modifier
                    .width(3.dp)
                    .fillMaxHeight()
                    .background(MaterialTheme.pixel.rule),
            )
            Column(
                modifier = Modifier.padding(start = 10.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                block.blocks.forEach { BlockView(it, colors.copy(text = colors.muted), style, depth) }
            }
        }
        is MdBlock.ListBlock -> ListView(block, colors, style, depth)
        is MdBlock.Table -> TableView(block, colors, style)
        MdBlock.Rule -> Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 4.dp)
                .height(2.dp)
                .background(MaterialTheme.colorScheme.outlineVariant),
        )
    }
}

@Composable
private fun InlineText(
    inlines: List<MdInline>,
    colors: MdColors,
    style: TextStyle,
    modifier: Modifier = Modifier,
    textAlign: TextAlign? = null,
) {
    val context = LocalContext.current
    val annotated = remember(inlines, colors) {
        buildAnnotatedString { appendInlines(inlines, colors) { url -> openExternalLink(context, url) } }
    }
    Text(
        text = annotated,
        style = if (textAlign != null) style.copy(textAlign = textAlign) else style,
        modifier = modifier,
    )
}

private fun AnnotatedString.Builder.appendInlines(
    inlines: List<MdInline>,
    colors: MdColors,
    onLink: (String) -> Unit,
) {
    inlines.forEach { inline ->
        when (inline) {
            is MdInline.Text -> append(inline.text)
            is MdInline.Code -> withStyle(
                SpanStyle(fontFamily = CodeFontFamily, background = colors.codeBackground, fontSize = 0.92.em()),
            ) { append(inline.text) }
            is MdInline.Bold -> withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) {
                appendInlines(inline.children, colors, onLink)
            }
            is MdInline.Italic -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) {
                appendInlines(inline.children, colors, onLink)
            }
            is MdInline.Strike -> withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough)) {
                appendInlines(inline.children, colors, onLink)
            }
            is MdInline.Link -> withLink(
                LinkAnnotation.Url(
                    url = inline.url,
                    styles = TextLinkStyles(
                        style = SpanStyle(color = colors.link, textDecoration = TextDecoration.Underline),
                    ),
                    linkInteractionListener = { onLink(inline.url) },
                ),
            ) { appendInlines(inline.children, colors, onLink) }
            MdInline.LineBreak -> append('\n')
        }
    }
}

private fun Double.em() = androidx.compose.ui.unit.TextUnit(this.toFloat(), androidx.compose.ui.unit.TextUnitType.Em)

@Composable
private fun ListView(block: MdBlock.ListBlock, colors: MdColors, style: TextStyle, depth: Int) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        block.items.forEachIndexed { index, item ->
            Row {
                val markerModifier = Modifier.widthIn(min = 22.dp).padding(end = 6.dp)
                when {
                    item.checked != null -> PixelCheckbox(
                        checked = item.checked,
                        onCheckedChange = null,
                        modifier = Modifier.size(22.dp).padding(end = 4.dp),
                    )
                    block.ordered -> Text(
                        text = "${block.start + index}.",
                        style = style.copy(color = colors.muted),
                        modifier = markerModifier,
                    )
                    else -> Text(
                        text = BULLETS[depth % BULLETS.size],
                        style = style.copy(color = colors.muted),
                        modifier = markerModifier,
                    )
                }
                Column(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    item.blocks.forEach { child ->
                        val nested = if (child is MdBlock.ListBlock) depth + 1 else depth
                        BlockView(child, colors, style, nested)
                    }
                }
            }
        }
    }
}

private val BULLETS = listOf("•", "◦", "▪")

@Composable
fun CodeBlock(language: String?, code: String, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val haptic = LocalHapticFeedback.current
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceContainerHighest)
            .pixelFrame(MaterialTheme.colorScheme.outlineVariant),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = language ?: "code",
                style = MaterialTheme.typography.bodySmall.copy(fontFamily = CodeFontFamily),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            IconButton(onClick = { copyWithFeedback(context, haptic, "Code", code) }) {
                Icon(
                    imageVector = Icons.Default.ContentCopy,
                    contentDescription = "Copy code",
                    modifier = Modifier.size(18.dp),
                )
            }
        }
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(start = 12.dp, end = 12.dp, bottom = 10.dp),
        ) {
            Text(
                text = code,
                style = MaterialTheme.typography.bodySmall.copy(
                    fontFamily = CodeFontFamily,
                    fontSize = 13.sp,
                    lineHeight = 18.sp,
                ),
                color = MaterialTheme.colorScheme.onSurface,
                softWrap = false,
            )
        }
    }
}

@Composable
private fun TableView(block: MdBlock.Table, colors: MdColors, style: TextStyle) {
    val columns = block.header.size
    if (columns == 0) return
    val zebra = MaterialTheme.colorScheme.surfaceContainerHighest
    val headerFill = MaterialTheme.pixel.surface2
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
    ) {
        Box(modifier = Modifier.pixelFrame(MaterialTheme.colorScheme.outlineVariant).padding(2.dp)) {
            Layout(
                content = {
                    block.header.forEachIndexed { c, cell ->
                        TableCell(cell, colors, style, block.aligns.getOrNull(c), header = true, fill = headerFill)
                    }
                    block.rows.forEachIndexed { r, row ->
                        row.forEachIndexed { c, cell ->
                            TableCell(
                                cell,
                                colors,
                                style,
                                block.aligns.getOrNull(c),
                                header = false,
                                fill = if (r % 2 == 1) zebra else Color.Transparent,
                            )
                        }
                    }
                },
            ) { measurables, _ ->
                val rows = measurables.size / columns
                val maxCell = 280.dp.roundToPx()
                val widths = IntArray(columns) { c ->
                    (0 until rows).maxOf { r -> measurables[r * columns + c].maxIntrinsicWidth(Constraints.Infinity) }
                        .coerceAtMost(maxCell)
                }
                val heights = IntArray(rows) { r ->
                    (0 until columns).maxOf { c -> measurables[r * columns + c].minIntrinsicHeight(widths[c]) }
                }
                val placeables = measurables.mapIndexed { i, m ->
                    m.measure(Constraints.fixed(widths[i % columns], heights[i / columns]))
                }
                layout(widths.sum(), heights.sum()) {
                    var y = 0
                    for (r in 0 until rows) {
                        var x = 0
                        for (c in 0 until columns) {
                            placeables[r * columns + c].place(x, y)
                            x += widths[c]
                        }
                        y += heights[r]
                    }
                }
            }
        }
    }
}

@Composable
private fun TableCell(
    inlines: List<MdInline>,
    colors: MdColors,
    style: TextStyle,
    align: MdAlign?,
    header: Boolean,
    fill: Color,
) {
    Box(
        modifier = Modifier
            .background(fill)
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        InlineText(
            inlines = inlines,
            colors = colors,
            style = style.copy(
                color = colors.text,
                fontWeight = if (header) FontWeight.SemiBold else style.fontWeight,
            ),
            textAlign = when (align) {
                MdAlign.Center -> TextAlign.Center
                MdAlign.End -> TextAlign.End
                else -> TextAlign.Start
            },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

private const val PREVIEW_MARKDOWN = """# Fix plan
I'll **look** at `login.py` first, then run [the tests](https://example.com).

- Read the code
  - nested *item*
- [x] Done task
- [ ] Open task

1. First
2. Second

> A quoted line

| Name | Count |
|:-----|------:|
| a    | 1     |
| b    | 22    |

```python
def hello():
    return 42
```
"""

@Preview(name = "Markdown light", showBackground = true)
@Preview(name = "Markdown dark", showBackground = true, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
private fun MarkdownPreview() {
    MobaideTheme {
        Column(modifier = Modifier.padding(16.dp).heightIn(max = 900.dp)) {
            MarkdownText(PREVIEW_MARKDOWN)
            Spacer(modifier = Modifier.height(8.dp))
        }
    }
}
