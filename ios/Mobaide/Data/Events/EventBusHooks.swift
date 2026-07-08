import Foundation

/// Repository hooks the user event bus routes into. Declared as protocols
/// so this issue can ship the bus with no-op stubs; the real
/// agent/template repositories implement them in iOS-06.
protocol AgentStatusApplying: Sendable {
    /// Patch the online flag of one agent in the local snapshot.
    func updateAgentOnline(agentId: String, online: Bool) async
    /// Full REST reconcile after (re)connect, recovering transitions
    /// missed while the socket was down.
    func refreshAgents() async throws
    /// Drop the cached snapshot on sign-out so a different account signing in
    /// the same process never sees the previous user's agents.
    func clear() async
}

protocol TemplateSyncApplying: Sendable {
    /// Insert-or-replace by `template_id`.
    func applyUpserted(_ template: TemplateDto) async
    /// Remove by `template_id`.
    func applyDeleted(templateId: String) async
    /// Full REST reconcile after (re)connect.
    func refreshTemplates() async throws
    /// Drop the cached snapshot on sign-out (cross-account isolation).
    func clear() async
}

struct NoOpAgentStatusApplier: AgentStatusApplying {
    func updateAgentOnline(agentId: String, online: Bool) async {}
    func refreshAgents() async throws {}
    func clear() async {}
}

struct NoOpTemplateSyncApplier: TemplateSyncApplying {
    func applyUpserted(_ template: TemplateDto) async {}
    func applyDeleted(templateId: String) async {}
    func refreshTemplates() async throws {}
    func clear() async {}
}
