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

protocol ShortcutSyncApplying: Sendable {
    /// Insert-or-replace by `shortcut_id`.
    func applyUpserted(_ shortcut: ShortcutDto) async
    /// Remove by `shortcut_id`.
    func applyDeleted(shortcutId: String) async
    /// Remove every cached shortcut of one wire kind — the client half of the
    /// server-side cascade on `custom_type.deleted`, which soft-deletes the
    /// type's shortcuts without emitting per-shortcut `shortcut.deleted` events.
    func purgeKind(_ kind: String) async
    /// Full REST reconcile after (re)connect.
    func refreshShortcuts() async throws
    /// Drop the cached snapshot on sign-out (cross-account isolation).
    func clear() async
}

protocol CustomTypeSyncApplying: Sendable {
    /// Insert-or-replace by `id`.
    func applyUpserted(_ type: CustomAgentTypeDto) async
    /// Remove by `id`.
    func applyDeleted(typeId: String) async
    /// Full REST reconcile after (re)connect.
    func refreshCustomTypes() async throws
    /// Drop the cached snapshot on sign-out (cross-account isolation).
    func clear() async
}

protocol FirstAgentPairedApplying: Sendable {
    /// Live `account.first_agent_paired`: the account just paired its first
    /// agent ever, on any client.
    func firstAgentPaired(agentId: String) async
    /// After every (re)connect: catch up on a first pairing that happened
    /// while the socket was down.
    func reconcileAfterConnect() async
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

struct NoOpShortcutSyncApplier: ShortcutSyncApplying {
    func applyUpserted(_ shortcut: ShortcutDto) async {}
    func applyDeleted(shortcutId: String) async {}
    func purgeKind(_ kind: String) async {}
    func refreshShortcuts() async throws {}
    func clear() async {}
}

struct NoOpCustomTypeSyncApplier: CustomTypeSyncApplying {
    func applyUpserted(_ type: CustomAgentTypeDto) async {}
    func applyDeleted(typeId: String) async {}
    func refreshCustomTypes() async throws {}
    func clear() async {}
}

struct NoOpFirstAgentPairedApplier: FirstAgentPairedApplying {
    func firstAgentPaired(agentId: String) async {}
    func reconcileAfterConnect() async {}
}
