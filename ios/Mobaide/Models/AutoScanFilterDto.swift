import Foundation

/// Every field has a default, so the whole object may arrive absent or
/// partial on the wire; all five decode via `decodeIfPresent ?? default`.
struct AutoScanFilterDto: Codable, Equatable, Sendable {
    let state_names: [String]
    let label_names: [String]
    let active_state: String
    let done_state: String
    let removal_label: String

    init(
        state_names: [String] = ["Proposals"],
        label_names: [String] = ["AutoPipeline"],
        active_state: String = "In Progress",
        done_state: String = "Done",
        removal_label: String = "AutoPipeline"
    ) {
        self.state_names = state_names
        self.label_names = label_names
        self.active_state = active_state
        self.done_state = done_state
        self.removal_label = removal_label
    }
}

extension AutoScanFilterDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        state_names = try container.decodeIfPresent([String].self, forKey: .state_names) ?? ["Proposals"]
        label_names = try container.decodeIfPresent([String].self, forKey: .label_names) ?? ["AutoPipeline"]
        active_state = try container.decodeIfPresent(String.self, forKey: .active_state) ?? "In Progress"
        done_state = try container.decodeIfPresent(String.self, forKey: .done_state) ?? "Done"
        removal_label = try container.decodeIfPresent(String.self, forKey: .removal_label) ?? "AutoPipeline"
    }
}

struct LinearLabelDto: Codable, Equatable, Sendable {
    let name: String
    let color: String

    init(name: String, color: String = "") {
        self.name = name
        self.color = color
    }
}

extension LinearLabelDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        color = try container.decodeIfPresent(String.self, forKey: .color) ?? ""
    }
}

struct LinearStateDto: Codable, Equatable, Sendable {
    let name: String
    let type: String

    init(name: String, type: String = "") {
        self.name = name
        self.type = type
    }
}

extension LinearStateDto {
    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        name = try container.decode(String.self, forKey: .name)
        type = try container.decodeIfPresent(String.self, forKey: .type) ?? ""
    }
}
