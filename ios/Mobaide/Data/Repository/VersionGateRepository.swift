import Foundation

/// Port of Android `VersionGateRepository`: probes `GET /api/version` before
/// the app connects and classifies whether this build can speak the server's
/// wire protocol. An incompatible verdict pins the terminal upgrade takeover
/// via `SessionManager.markIncompatibleServer`; a probe that cannot complete
/// is `unknown` and never blocks (Android parity — the WS-4002 close is the
/// backstop when the gate races a live server upgrade).
struct VersionGateRepository: Sendable {
    /// Outcome of classifying a `/api/version` response against this build's
    /// protocol version.
    enum ServerCompatibility: Equatable, Sendable {
        case compatible(serverVersion: String, protocolRange: [Int], latestAgentVersion: String?)
        case incompatible(reason: String, minClientVersion: Int)
        /// The probe could not complete (network / transport error). Not a
        /// blocking verdict.
        case unknown
    }

    let centralAPI: CentralAPI
    let sessionManager: SessionManager
    /// This build's wire-protocol version. Injected so tests can drive the
    /// whole compatibility matrix without rebuilding the bundle.
    let appProtocolVersion: Int

    init(
        centralAPI: CentralAPI,
        sessionManager: SessionManager,
        appProtocolVersion: Int = ProtocolVersion.app
    ) {
        self.centralAPI = centralAPI
        self.sessionManager = sessionManager
        self.appProtocolVersion = appProtocolVersion
    }

    /// Fetch and classify the server version; on an incompatible verdict pin
    /// the takeover state. Returns the verdict for callers and tests.
    @discardableResult
    func probe() async -> ServerCompatibility {
        let outcome: ServerCompatibility
        do {
            outcome = classify(try await centralAPI.getVersion())
        } catch {
            outcome = .unknown
        }
        if case let .incompatible(reason, minClientVersion) = outcome {
            await sessionManager.markIncompatibleServer(
                AuthState.IncompatibleServer(
                    reason: reason,
                    minClientVersion: minClientVersion,
                    storeUrl: AppConfig.appStoreURL,
                    storeFallbackUrl: AppConfig.appStoreFallbackURL
                )
            )
        }
        return outcome
    }

    /// Compatible iff this build's protocol version is at least the server's
    /// `min_client_version` **and** is one of the server's advertised
    /// `protocol_versions` — exact set membership, not a range check.
    func classify(_ info: VersionInfoResponse) -> ServerCompatibility {
        let app = appProtocolVersion
        let acceptable = app >= info.min_client_version && info.protocol_versions.contains(app)
        guard acceptable else {
            return .incompatible(
                reason: buildReason(app: app, info: info),
                minClientVersion: info.min_client_version
            )
        }
        return .compatible(
            serverVersion: info.server_version,
            protocolRange: info.protocol_versions,
            latestAgentVersion: info.latest_agent_version
        )
    }

    private func buildReason(app: Int, info: VersionInfoResponse) -> String {
        "Server requires protocol v\(info.min_client_version)+, this app speaks v\(app). Server protocols: \(info.protocol_versions)."
    }
}
