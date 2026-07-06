import Foundation

struct TemplateDto: Codable, Equatable, Sendable {
    let template_id: String
    let name: String
    let body: String
    let is_favorite: Bool
    let last_used_at: String?
    let created_at: String
    let updated_at: String
    let version: Int
}

struct CreateTemplateRequest: Codable, Equatable, Sendable {
    let name: String
    let body: String
}

struct UpdateTemplateRequest: Codable, Equatable, Sendable {
    let name: String?
    let body: String?
    let is_favorite: Bool?

    init(name: String? = nil, body: String? = nil, is_favorite: Bool? = nil) {
        self.name = name
        self.body = body
        self.is_favorite = is_favorite
    }
}

struct TemplateDeletedPayload: Codable, Equatable, Sendable {
    let template_id: String
    let version: Int
    let updated_at: String
}
