package com.mobaide.app.ui.agents

import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import com.google.android.gms.common.moduleinstall.ModuleInstall
import com.google.android.gms.common.moduleinstall.ModuleInstallRequest
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScanner
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning

/**
 * Composable launcher that wraps Google's Code Scanner.
 *
 * The Code Scanner module renders its own UI in a separate process, so
 * this app does NOT need the `CAMERA` permission. The first scan on a
 * device may trigger a one-time Play Services module install — surface
 * that to the user via [QrScanLauncher.warmUp].
 */
data class ParsedQrPairingPayload(
    val version: String,
    val relayUrl: String,
    val nonce: String,
    val trustKeyFp: String,
    val expUnix: Long,
)

/** Parse `MOBA1|relay|nonce|fp|exp`. Returns null on any malformedness. */
fun parseMoba1(raw: String): ParsedQrPairingPayload? {
    val parts = raw.split("|")
    if (parts.size != 5) return null
    val (version, relayUrl, nonce, trustKeyFp, expStr) = parts
    if (version != "MOBA1") return null
    if (relayUrl.isBlank() || nonce.isBlank() || trustKeyFp.isBlank()) return null
    val expUnix = expStr.toLongOrNull() ?: return null
    return ParsedQrPairingPayload(
        version = version,
        relayUrl = relayUrl,
        nonce = nonce,
        trustKeyFp = trustKeyFp,
        expUnix = expUnix,
    )
}

class QrScanLauncher internal constructor(
    private val scanner: GmsBarcodeScanner,
    private val moduleInstaller: () -> Unit,
) {
    /**
     * Trigger the Code Scanner module install proactively, if not already
     * cached on-device. Best-effort; failures are silently dropped — the
     * scanner still works on first scan, just with a brief UX delay.
     */
    fun warmUp() = moduleInstaller()

    fun launch(onResult: (Result<ParsedQrPairingPayload>) -> Unit) {
        scanner.startScan()
            .addOnSuccessListener { barcode ->
                val raw = barcode.rawValue
                if (raw == null) {
                    onResult(Result.failure(IllegalStateException("scanner returned no payload")))
                    return@addOnSuccessListener
                }
                val parsed = parseMoba1(raw)
                if (parsed == null) {
                    onResult(Result.failure(IllegalArgumentException(
                        "Scanned QR is not a Mobaide pairing code"
                    )))
                    return@addOnSuccessListener
                }
                onResult(Result.success(parsed))
            }
            .addOnCanceledListener {
                onResult(Result.failure(CancellationException("scan cancelled")))
            }
            .addOnFailureListener { exc ->
                onResult(Result.failure(exc))
            }
    }
}

/** Wraps the platform scanner so the cancel-from-user flow surfaces cleanly. */
class CancellationException(message: String) : Exception(message)

@Composable
fun rememberQrPairingScanner(): QrScanLauncher {
    val context = LocalContext.current
    return remember(context) {
        val options = GmsBarcodeScannerOptions.Builder()
            .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
            .enableAutoZoom()
            .build()
        val scanner = GmsBarcodeScanning.getClient(context, options)
        val moduleInstaller: () -> Unit = {
            val request = ModuleInstallRequest.newBuilder()
                .addApi(scanner)
                .build()
            ModuleInstall.getClient(context)
                .installModules(request)
        }
        QrScanLauncher(scanner = scanner, moduleInstaller = moduleInstaller)
    }
}
