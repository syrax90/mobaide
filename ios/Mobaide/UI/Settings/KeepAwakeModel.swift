import Observation

/// Keep awake for the connected agent. The status lives in
/// `AgentSettingsRepository`, which `agent_settings.changed` keeps fresh, so a
/// change made on another device, or a lock the computer lost, shows at once.
@MainActor
@Observable
final class KeepAwakeModel {
    private static let unreachablePollSeconds: UInt64 = 5

    enum Availability: Equatable {
        /// No agent is selected: the section says to connect to one.
        case noAgent
        case checking
        /// The agent did not answer.
        case unreachable
        /// The agent predates the setting (no `capabilities.agent_settings.keep_awake`).
        case outdated
        case available
    }

    private(set) var availability: Availability
    /// The value being saved: the box shows it until the agent answers.
    private(set) var saving: Bool?
    /// The agent refused or missed the last change; the box shows its old value.
    private(set) var saveFailed = false
    let agentName: String

    @ObservationIgnored private let repository: AgentSettingsRepository
    @ObservationIgnored private var probe: Task<Void, Never>?

    init(container: AppContainer) {
        repository = container.agentSettingsRepository
        let agentId = container.tokenStore.getAgentId()
        agentName = agentId.flatMap { id in
            container.agentRepository.currentAgents.first(where: { $0.agent_id == id })?.name
        } ?? VoiceInputModel.defaultAgentName
        availability = container.tokenStore.getAgentScopeKey() == nil ? .noAgent : .checking
    }

    var status: KeepAwakeStatus? { repository.settings?.keep_awake }

    /// Nothing says that an agent which did not answer is back, so while it
    /// does not answer and the section is on screen (the caller's
    /// `.task(id:)` scopes it), it is asked again, like the voice section.
    func pollWhileUnreachable() async {
        while availability == .unreachable, !Task.isCancelled {
            try? await Task.sleep(nanoseconds: Self.unreachablePollSeconds * 1_000_000_000)
            guard !Task.isCancelled else { return }
            await load()
        }
    }

    /// Asks the agent again; the section calls it each time it comes on screen.
    /// One at a time: a caller during a probe waits for that probe.
    func load() async {
        guard availability != .noAgent else { return }
        if let probe { return await probe.value }
        let task = Task { @MainActor in
            defer { self.probe = nil }
            let supported: Bool
            do {
                supported = try await self.repository.keepAwakeSupported()
            } catch is AgentURLNotConfiguredError {
                self.availability = .noAgent
                return
            } catch {
                self.availability = .unreachable
                return
            }
            self.availability = supported ? .available : .outdated
            guard supported else { return }
            do {
                try await self.repository.refresh()
            } catch {
                if self.repository.settings == nil { self.availability = .unreachable }
            }
        }
        probe = task
        await task.value
    }

    func setKeepAwake(_ on: Bool) {
        guard saving == nil else { return }
        saving = on
        saveFailed = false
        Task {
            do {
                try await repository.setKeepAwake(on)
            } catch {
                saveFailed = true
            }
            saving = nil
        }
    }
}
