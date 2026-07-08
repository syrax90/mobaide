import SwiftUI
@preconcurrency import AVFoundation
@preconcurrency import VisionKit

/// In-app QR scanner sheet. iOS requires camera access + an explainer (unlike
/// Android's out-of-process GMS scanner). Prefers VisionKit's
/// `DataScannerViewController`; falls back to an AVFoundation metadata scanner
/// when VisionKit is unsupported (pre-A12 / no Neural Engine). Emits the raw QR
/// string once; parsing/validation happens in `AgentListModel`.
struct QrScannerView: View {
    let onScan: (String) -> Void
    let onCancel: () -> Void

    @State private var status = AVCaptureDevice.authorizationStatus(for: .video)

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Scan pairing QR")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { onCancel() }
                    }
                }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch status {
        case .authorized:
            scanner
        case .notDetermined:
            explainer
        default:
            denied
        }
    }

    @ViewBuilder
    private var scanner: some View {
        if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
            DataScannerRepresentable(onScan: onScan)
                .ignoresSafeArea()
        } else {
            MetadataScannerRepresentable(onScan: onScan)
                .ignoresSafeArea()
        }
    }

    private var explainer: some View {
        VStack(spacing: 20) {
            Image(systemName: "qrcode.viewfinder")
                .font(.system(size: 56))
                .foregroundStyle(.tint)
            Text("Camera access needed")
                .font(.title3.bold())
            Text("Mobaide uses the camera only to read the pairing QR rendered on your agent host. The code is never uploaded.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Button("Allow camera access") {
                AVCaptureDevice.requestAccess(for: .video) { granted in
                    Task { @MainActor in
                        status = granted ? .authorized : .denied
                    }
                }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(32)
    }

    private var denied: some View {
        VStack(spacing: 20) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 48))
                .foregroundStyle(MobaideColors.warningYellow)
            Text("Camera access is off")
                .font(.title3.bold())
            Text("Enable camera access for Mobaide in Settings to scan a pairing QR.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            if let url = URL(string: UIApplication.openSettingsURLString) {
                Link("Open Settings", destination: url)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(32)
    }
}

// MARK: - VisionKit

private struct DataScannerRepresentable: UIViewControllerRepresentable {
    let onScan: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let scanner = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.qr])],
            qualityLevel: .balanced,
            recognizesMultipleItems: false,
            isHighFrameRateTrackingEnabled: false,
            isHighlightingEnabled: true
        )
        scanner.delegate = context.coordinator
        return scanner
    }

    func updateUIViewController(_ scanner: DataScannerViewController, context: Context) {
        try? scanner.startScanning()
    }

    func makeCoordinator() -> Coordinator { Coordinator(onScan: onScan) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        private let onScan: (String) -> Void
        private var handled = false

        init(onScan: @escaping (String) -> Void) { self.onScan = onScan }

        func dataScanner(_ scanner: DataScannerViewController, didAdd addedItems: [RecognizedItem], allItems: [RecognizedItem]) {
            deliverFirstBarcode(from: addedItems, scanner: scanner)
        }

        func dataScanner(_ scanner: DataScannerViewController, didTapOn item: RecognizedItem) {
            deliverFirstBarcode(from: [item], scanner: scanner)
        }

        private func deliverFirstBarcode(from items: [RecognizedItem], scanner: DataScannerViewController) {
            guard !handled else { return }
            for case .barcode(let barcode) in items {
                guard let value = barcode.payloadStringValue else { continue }
                handled = true
                scanner.stopScanning()
                onScan(value)
                return
            }
        }
    }
}

// MARK: - AVFoundation fallback

private struct MetadataScannerRepresentable: UIViewControllerRepresentable {
    let onScan: (String) -> Void

    func makeUIViewController(context: Context) -> MetadataScannerViewController {
        let controller = MetadataScannerViewController()
        controller.onScan = onScan
        return controller
    }

    func updateUIViewController(_ controller: MetadataScannerViewController, context: Context) {}
}

private final class MetadataScannerViewController: UIViewController, @MainActor AVCaptureMetadataOutputObjectsDelegate {
    var onScan: ((String) -> Void)?

    private let session = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "com.mobaide.qr-scanner.session")
    private lazy var previewLayer = AVCaptureVideoPreviewLayer(session: session)
    private var handled = false

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        configureSession()
        previewLayer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(previewLayer)
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        previewLayer.frame = view.bounds
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        sessionQueue.async { [session] in
            if !session.isRunning { session.startRunning() }
        }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        sessionQueue.async { [session] in
            if session.isRunning { session.stopRunning() }
        }
    }

    private func configureSession() {
        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input)
        else { return }
        session.addInput(input)

        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { return }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = [.qr]
    }

    func metadataOutput(
        _ output: AVCaptureMetadataOutput,
        didOutput metadataObjects: [AVMetadataObject],
        from connection: AVCaptureConnection
    ) {
        guard !handled,
              let code = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
              code.type == .qr,
              let value = code.stringValue
        else { return }
        handled = true
        sessionQueue.async { [session] in session.stopRunning() }
        onScan?(value)
    }
}
