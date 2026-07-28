import Foundation

/// Wire-JSON conventions ported from the Android kotlinx.serialization configs.
///
/// Parity mapping (see `RestApi.kt` / `CentralApi.kt`):
/// - `ignoreUnknownKeys = true` — `JSONDecoder` ignores unknown keys by default.
/// - `coerceInputValues = true` — replicated per type: every Kotlin-defaulted
///   field decodes via `decodeIfPresent ?? default` (an explicit-null `kind`
///   still falls back to `.claude`; unknown kind strings are preserved
///   verbatim as `SessionKind.custom` since proto-v7).
/// - `explicitNulls = false; encodeDefaults = false` (the `ProjectUpdate`
///   PATCH encoder) — synthesized `encodeIfPresent` omits nil optionals, so
///   the three-state omit semantics hold with the plain encoder.
/// - Wire keys are the property names verbatim (snake_case); no key strategy.
enum WireJSON {
    static func decoder() -> JSONDecoder { JSONDecoder() }
    static func encoder() -> JSONEncoder { JSONEncoder() }
}
