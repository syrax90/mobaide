import Foundation

/// Tracks DEC private mode 2026 (synchronized output) across the raw output
/// stream, so row hit-testing can fail closed while the emulator has stopped
/// painting the buffer it is still parsing into.
///
/// SwiftTerm keeps one live buffer and suppresses paints while the mode is set,
/// until `ESC[?2026l` or a one-second timeout — so the rows on screen lag the
/// rows a lookup reads for as long as it lasts.
///
/// This scanner exists because that state used to be unreadable: SwiftTerm 1.13
/// exposed neither the flag nor `displayBuffer`, leaving the byte stream the app
/// already holds as the only signal. Since 1.15 `Terminal.synchronizedOutputActive`
/// is `public private(set)`, so the scanner is retained only until that direct
/// read is adopted — the hazard it guards is unchanged, but the reason it has to
/// be reproduced here is gone.
///
/// The machine reproduces the CSI path of SwiftTerm's `EscapeSequenceParser`
/// rather than approximating it, because the two directions need opposite
/// margins and only fidelity affords both: a missed `ESC[?2026h` leaves row
/// lookups reading a buffer nobody is drawing, and a spurious `ESC[?2026l`
/// reopens them just as wide. Arming may over-fire, clearing may not — the one
/// deliberate divergence, treating `0x9B` as a CSI introducer even from ground
/// where SwiftTerm prints it, is therefore allowed to arm but never to disarm.
/// Once the two machines are out of step that way they stay out of step until
/// an `ESC` resynchronises them, so the ban on disarming is inherited by every
/// sequence started in between rather than re-derived per sequence.
///
/// `ESC` restarts the machine from every state, which is why OSC, DCS and APC
/// strings need no modelling: a sequence reaching DECSET from inside one must
/// pass through `ESC` or `0x9B` first. State survives across calls, so a
/// sequence split over two WebSocket frames still matches.
///
/// Android needs no scanner — its renderer and row lookup share one screen — so
/// the counterpart gate there is `TerminalSession.isScreenUpdateDeferred()`,
/// which watches the deferred *paint* instead.
struct TerminalSynchronizedOutput {
    /// `Terminal.synchronizedOutputTimeoutSeconds`: SwiftTerm drops the snapshot
    /// this long after the arming `ESC[?2026h`, and every further one reschedules
    /// the expiry without taking a new snapshot.
    static let emulatorTimeout: TimeInterval = 1.0

    /// Slack on top of `emulatorTimeout`. SwiftTerm expires the snapshot from a
    /// main-queue `DispatchWorkItem`, which runs no earlier than its deadline and
    /// can be preceded by a tap callback already queued on the same runloop — so
    /// the gate has to outlive the snapshot rather than race it.
    static let expiryGrace: TimeInterval = 0.25

    private static let synchronizedOutputMode = 2026
    private static let privateMarker = UInt8(ascii: "?")

    private enum State {
        case ground
        case escape
        case csiEntry
        case csiParameters
        case csiIntermediate
        /// SwiftTerm's `csiIgnore`: the sequence is already malformed, and its
        /// final byte is swallowed without ever reaching `setMode`.
        case csiIgnore
    }

    private var state: State = .ground
    private var collectedCount = 0
    private var firstCollected: UInt8 = 0
    private var mayDisarm = false
    private var parameter = 0
    private var sawTargetParameter = false
    private var deadline: TimeInterval?

    /// Whether the emulator may still be drawing a snapshot rather than the
    /// buffer a row lookup reads.
    func isActive(at now: TimeInterval) -> Bool {
        guard let deadline else { return false }
        return now < deadline
    }

    /// Scans emulator output for mode 2026 transitions.
    ///
    /// Call this *after* handing the same bytes to SwiftTerm: both are
    /// synchronous on the main actor so no tap can interleave, and timestamping
    /// afterwards puts this deadline no earlier than the emulator's own — which a
    /// large replay frame carrying the arming `ESC[?2026h` near its end would
    /// otherwise beat by the parse time of everything ahead of it.
    mutating func scan(_ bytes: ArraySlice<UInt8>, at now: TimeInterval) {
        var index = bytes.startIndex
        while index < bytes.endIndex {
            // A normal transcript is almost entirely ground, and only these two
            // bytes leave it.
            if state == .ground {
                guard let next = bytes[index...].firstIndex(where: { $0 == 0x1B || $0 == 0x9B }) else { return }
                index = next
            }
            consume(bytes[index], at: now)
            index = bytes.index(after: index)
        }
    }

    private mutating func consume(_ byte: UInt8, at now: TimeInterval) {
        if byte == 0x1B {
            state = .escape
            // `ESC` reaches the escape state from every one of SwiftTerm's states,
            // so it is the one byte that puts the two machines provably back in
            // step — and the only thing that clears the taint set below.
            mayDisarm = true
            return
        }
        // SwiftTerm maps 0x9B to CSI entry from every state but ground, where the
        // parser's print fast path swallows it first. Accepting it from ground
        // too covers `ESC ] 0x9B ?2026h`, which does arm the mode, without
        // modelling the string states. The CSI state it opens there is ours
        // alone, though — and so is every sequence a further 0x9B starts from
        // inside it, since SwiftTerm is still printing. The taint therefore has
        // to be inherited rather than recomputed from the scanner's own state.
        if byte == 0x9B {
            beginControlSequence(mayDisarm: state != .ground && mayDisarm)
            return
        }
        // SwiftTerm re-adds its `executables` per state after the global rules,
        // so these are executed in place and leave the sequence intact. SUB
        // (0x1A) keeps the global entry and is the only C0 byte that aborts one —
        // CAN (0x18) does not, despite the convention.
        if byte <= 0x18 || (0x1C...0x1F).contains(byte) || byte == 0x7F {
            return
        }
        if byte == 0x1A {
            state = .ground
            return
        }
        switch state {
        case .ground:
            break
        case .escape:
            if byte == UInt8(ascii: "[") {
                beginControlSequence(mayDisarm: true)
            } else {
                // Every other escape either dispatches immediately or opens a
                // string state, neither of which can reach DECSET without passing
                // through `ESC` or `0x9B` again.
                state = .ground
            }
        case .csiEntry:
            switch byte {
            case 0x30...0x39:
                accumulate(byte)
                state = .csiParameters
            case 0x3B:
                endParameter()
                state = .csiParameters
            case 0x3C...0x3F:
                collect(byte)
                state = .csiParameters
            case 0x20...0x2F:
                collect(byte)
                state = .csiIntermediate
            case 0x40...0x7E:
                dispatch(final: byte, at: now)
            default:
                state = .ground
            }
        case .csiParameters:
            switch byte {
            case 0x30...0x39:
                accumulate(byte)
            case 0x3A, 0x3B:
                endParameter()
            case 0x3C...0x3F:
                // A prefix byte once the parameters have begun is malformed:
                // `ESC[1;?2026l` never reaches `resetMode`.
                state = .csiIgnore
            case 0x20...0x2F:
                collect(byte)
                state = .csiIntermediate
            case 0x40...0x7E:
                dispatch(final: byte, at: now)
            default:
                state = .ground
            }
        case .csiIntermediate:
            switch byte {
            case 0x20...0x2F:
                collect(byte)
            case 0x30...0x3F:
                state = .csiIgnore
            case 0x40...0x7E:
                dispatch(final: byte, at: now)
            default:
                state = .ground
            }
        case .csiIgnore:
            switch byte {
            case 0x20...0x3F:
                break
            default:
                state = .ground
            }
        }
    }

    private mutating func beginControlSequence(mayDisarm: Bool) {
        state = .csiEntry
        collectedCount = 0
        firstCollected = 0
        parameter = 0
        sawTargetParameter = false
        self.mayDisarm = mayDisarm
    }

    /// Saturates at two: `setMode` wants a collect array of exactly one `?`, so
    /// nothing past the second byte changes the answer.
    private mutating func collect(_ byte: UInt8) {
        if collectedCount == 0 { firstCollected = byte }
        if collectedCount < 2 { collectedCount += 1 }
    }

    /// SwiftTerm's own accumulation, overflow quirk included: past the threshold
    /// it restarts from zero rather than clamping, so a parameter long enough to
    /// overflow and then spelling `2026` really does reach `cmdSetMode` as 2026.
    private mutating func accumulate(_ digit: UInt8) {
        let next = parameter * 10 + Int(digit) - 48
        parameter = next > (Int.max / 10) - 10 ? 0 : next
    }

    private mutating func endParameter() {
        if parameter == Self.synchronizedOutputMode { sawTargetParameter = true }
        parameter = 0
    }

    private mutating func dispatch(final: UInt8, at now: TimeInterval) {
        endParameter()
        state = .ground
        // `setMode` and `resetMode` compare the collected prefix for equality
        // with `?`, and `cmdSetMode` fans every parameter out one by one — so
        // `ESC[?1049;2026h` arms just as a lone `ESC[?2026h` does, while
        // `ESC[?2026$l` reaches neither.
        guard collectedCount == 1, firstCollected == Self.privateMarker, sawTargetParameter else { return }
        switch final {
        case UInt8(ascii: "h"):
            deadline = now + Self.emulatorTimeout + Self.expiryGrace
        case UInt8(ascii: "l"):
            guard mayDisarm else { return }
            // Mirrors `endSynchronizedOutput()` cancelling its timeout item. Ink
            // emits BSU/ESU around every frame, so anything less than an
            // immediate clear here would leave row taps dead for whole sessions.
            deadline = nil
        default:
            break
        }
    }
}
