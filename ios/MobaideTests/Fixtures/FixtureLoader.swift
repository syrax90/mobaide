import Foundation
@testable import Mobaide

struct DTOFixtureFile: Decodable {
    let type: String
    let cases: [DTOFixtureCase]
}

struct DTOFixtureCase: Decodable, Sendable {
    let name: String
    /// The JSON as it appears on the wire (input to decoding).
    let wire: JSONValue
    /// Expected re-encoded JSON after a decode→encode round trip; defaults to
    /// `wire`. Differs when decoding materializes Kotlin defaults or coerces
    /// enum values.
    let decoded_canonical: JSONValue?
}

enum FixtureLoaderError: Error, CustomStringConvertible {
    case rootNotFound
    case fileNotFound(String)

    var description: String {
        switch self {
        case .rootNotFound:
            return "protocol_fixtures folder missing from test bundle — check the folder reference in project.pbxproj"
        case .fileNotFound(let path):
            return "fixture not found: \(path)"
        }
    }
}

enum FixtureLoader {
    private final class BundleToken {}

    static func fixturesRoot() throws -> URL {
        guard let root = Bundle(for: BundleToken.self).url(forResource: "protocol_fixtures", withExtension: nil) else {
            throw FixtureLoaderError.rootNotFound
        }
        return root
    }

    static func dtoFixtureURLs() throws -> [URL] {
        let dtoRoot = try fixturesRoot().appendingPathComponent("dto", isDirectory: true)
        guard let enumerator = FileManager.default.enumerator(at: dtoRoot, includingPropertiesForKeys: nil) else {
            throw FixtureLoaderError.fileNotFound("dto/")
        }
        return enumerator
            .compactMap { $0 as? URL }
            .filter { $0.pathExtension == "json" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    static func load<T: Decodable>(_ type: T.Type, at url: URL) throws -> T {
        let data = try Data(contentsOf: url)
        return try WireJSON.decoder().decode(T.self, from: data)
    }

    static func load<T: Decodable>(_ type: T.Type, path: String) throws -> T {
        let url = try fixturesRoot().appendingPathComponent(path)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw FixtureLoaderError.fileNotFound(path)
        }
        return try load(type, at: url)
    }
}

extension Data {
    init?(hexString: String) {
        guard hexString.count.isMultiple(of: 2) else { return nil }
        var data = Data(capacity: hexString.count / 2)
        var index = hexString.startIndex
        while index < hexString.endIndex {
            let next = hexString.index(index, offsetBy: 2)
            guard let byte = UInt8(hexString[index..<next], radix: 16) else { return nil }
            data.append(byte)
            index = next
        }
        self = data
    }

    var hexString: String {
        map { String(format: "%02x", $0) }.joined()
    }
}
