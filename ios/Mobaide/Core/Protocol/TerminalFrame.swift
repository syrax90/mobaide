import Foundation

/// Binary terminal frame grammar shared with the agent (PROTOCOL.md; Android
/// `TerminalProtocol.kt`): every WebSocket binary message is one frame — a
/// single type byte at `[0]` followed by the raw payload at `[1...]`.
/// The client encodes `i`/`x`/`m`/`r`/`c` and decodes `o`/`s`/`b`/`e`; an
/// empty frame or any other type byte decodes to nil.
enum TerminalFrameType {
    static let output: UInt8 = 0x6F     // 'o' server → client: raw PTY bytes
    static let input: UInt8 = 0x69      // 'i' client → server: raw input bytes
    static let execute: UInt8 = 0x78    // 'x' client → server: Ctrl+C, then payload, then CR
    static let submit: UInt8 = 0x6D     // 'm' client → server: payload, then CR as a separate write
    static let resize: UInt8 = 0x72     // 'r' client → server: u16 BE rows, u16 BE cols
    static let clearReplay: UInt8 = 0x63 // 'c' client → server: drop ring + scrollback, empty payload
    static let state: UInt8 = 0x73      // 's' server → client: UTF-8 JSON {status, pid?, exit_code?}
    static let replayStart: UInt8 = 0x62 // 'b' server → client: replay-buffer start, empty payload
    static let replayEnd: UInt8 = 0x65  // 'e' server → client: replay-buffer end, empty payload
}

enum TerminalOutboundFrame: Equatable, Sendable {
    case input(Data)
    case execute(Data)
    case submit(Data)
    /// Encodes as exactly 5 bytes: `[0x72, rows>>8, rows&0xFF, cols>>8, cols&0xFF]`.
    case resize(rows: UInt16, cols: UInt16)
    /// The server empties the session's replay ring and REST scrollback so a
    /// reconnect cannot resurrect a cleared screen. Type byte only.
    case clearReplay

    var encoded: Data {
        switch self {
        case .input(let payload):
            return Data([TerminalFrameType.input]) + payload
        case .execute(let payload):
            return Data([TerminalFrameType.execute]) + payload
        case .submit(let payload):
            return Data([TerminalFrameType.submit]) + payload
        case .resize(let rows, let cols):
            return Data([
                TerminalFrameType.resize,
                UInt8(rows >> 8), UInt8(rows & 0xFF),
                UInt8(cols >> 8), UInt8(cols & 0xFF),
            ])
        case .clearReplay:
            return Data([TerminalFrameType.clearReplay])
        }
    }

    static func input(_ text: String) -> TerminalOutboundFrame {
        .input(Data(text.utf8))
    }

    static func execute(_ text: String) -> TerminalOutboundFrame {
        .execute(Data(text.utf8))
    }

    static func submit(_ text: String) -> TerminalOutboundFrame {
        .submit(Data(text.utf8))
    }
}

enum TerminalInboundFrame: Equatable, Sendable {
    case output(Data)
    /// Raw UTF-8 JSON payload, kept unparsed to mirror `TerminalFrame.State(json)`.
    case state(json: String)
    case replayStart
    case replayEnd

    static func decode(_ frame: Data) -> TerminalInboundFrame? {
        guard let type = frame.first else { return nil }
        let payload = frame.dropFirst()
        switch type {
        case TerminalFrameType.output:
            return .output(Data(payload))
        case TerminalFrameType.state:
            return .state(json: String(decoding: payload, as: UTF8.self))
        case TerminalFrameType.replayStart:
            return .replayStart
        case TerminalFrameType.replayEnd:
            return .replayEnd
        default:
            return nil
        }
    }
}
