import Foundation

enum ProtocolVersion {
    static let app: Int = value("AppProtocolVersion")
    static let minServer: Int = value("MinServerProtocolVersion")

    static func value(_ key: String, bundle: Bundle = .main) -> Int {
        guard let raw = bundle.object(forInfoDictionaryKey: key) as? String,
              let version = Int(raw), version > 0 else {
            preconditionFailure(
                "Protocol constant \(key) missing or invalid — check ios/Config/Protocol.xcconfig → Info.plist wiring"
            )
        }
        return version
    }
}
