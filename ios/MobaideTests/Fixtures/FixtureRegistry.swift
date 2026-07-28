import Foundation
@testable import Mobaide

struct FixtureMismatch: Error, CustomStringConvertible {
    let typeName: String
    let caseName: String
    let expected: JSONValue
    let actual: JSONValue

    var description: String {
        "\(typeName).\(caseName) round-trip mismatch:\n  expected: \(expected)\n  actual:   \(actual)"
    }
}

/// Maps fixture `type` names to type-erased round-trip runners. The DTO
/// fixture test asserts bidirectionally: every fixture file has a runner and
/// every registered type has at least one fixture file.
enum FixtureRegistry {
    struct Runner: Sendable {
        let run: @Sendable (String, DTOFixtureCase) throws -> Void
    }

    static func runner<T: Codable>(_ type: T.Type) -> Runner {
        Runner { typeName, fixtureCase in
            let wireData = try WireJSON.encoder().encode(fixtureCase.wire)
            let decoded = try WireJSON.decoder().decode(T.self, from: wireData)
            let reencoded = try WireJSON.encoder().encode(decoded)
            let actual = try WireJSON.decoder().decode(JSONValue.self, from: reencoded)
            let expected = fixtureCase.decoded_canonical ?? fixtureCase.wire
            guard actual == expected else {
                throw FixtureMismatch(
                    typeName: typeName,
                    caseName: fixtureCase.name,
                    expected: expected,
                    actual: actual
                )
            }
        }
    }

    static let all: [String: Runner] = [
        // AgentDto.swift
        "AgentSummaryResponse": runner(AgentSummaryResponse.self),
        "CreateAgentRequest": runner(CreateAgentRequest.self),
        "CreateAgentResponse": runner(CreateAgentResponse.self),
        "ReRegisterAgentResponse": runner(ReRegisterAgentResponse.self),
        "MobileTokenResponse": runner(MobileTokenResponse.self),
        "MobileTokenRequest": runner(MobileTokenRequest.self),
        // AntigravityOptionsModel.swift
        "AntigravityOptionsModel": runner(AntigravityOptionsModel.self),
        // AuthDto.swift
        "TokenRequest": runner(TokenRequest.self),
        "TokenResponse": runner(TokenResponse.self),
        "RefreshRequest": runner(RefreshRequest.self),
        "UserRegisterRequest": runner(UserRegisterRequest.self),
        "UserLoginRequest": runner(UserLoginRequest.self),
        "GoogleAuthRequest": runner(GoogleAuthRequest.self),
        "UserAuthResponse": runner(UserAuthResponse.self),
        "VerifyEmailRequest": runner(VerifyEmailRequest.self),
        "ResendVerificationRequest": runner(ResendVerificationRequest.self),
        "ResendVerificationResponse": runner(ResendVerificationResponse.self),
        "VerificationRequiredDetail": runner(VerificationRequiredDetail.self),
        "VerificationRequiredErrorBody": runner(VerificationRequiredErrorBody.self),
        "ResendRateLimitedDetail": runner(ResendRateLimitedDetail.self),
        "ResendRateLimitedErrorBody": runner(ResendRateLimitedErrorBody.self),
        "ForgotPasswordRequest": runner(ForgotPasswordRequest.self),
        "ForgotPasswordResponse": runner(ForgotPasswordResponse.self),
        "ResetPasswordRequest": runner(ResetPasswordRequest.self),
        "ResetPasswordResponse": runner(ResetPasswordResponse.self),
        "ResetIpRateLimitedDetail": runner(ResetIpRateLimitedDetail.self),
        "ResetIpRateLimitedErrorBody": runner(ResetIpRateLimitedErrorBody.self),
        "ResetErrorDetail": runner(ResetErrorDetail.self),
        "ResetErrorBody": runner(ResetErrorBody.self),
        "DeleteAccountRequest": runner(DeleteAccountRequest.self),
        "CancelAccountDeletionResponse": runner(CancelAccountDeletionResponse.self),
        "DeleteAccountReauthDetail": runner(DeleteAccountReauthDetail.self),
        "DeleteAccountReauthErrorBody": runner(DeleteAccountReauthErrorBody.self),
        // AutoScanDto.swift
        "AutoScanInstanceDto": runner(AutoScanInstanceDto.self),
        "AutoScanListResponse": runner(AutoScanListResponse.self),
        "StartScanRequest": runner(StartScanRequest.self),
        "StopScanRequest": runner(StopScanRequest.self),
        "RunNowRequest": runner(RunNowRequest.self),
        "RunNowResponse": runner(RunNowResponse.self),
        // AutoScanFilterDto.swift
        "AutoScanFilterDto": runner(AutoScanFilterDto.self),
        "LinearLabelDto": runner(LinearLabelDto.self),
        "LinearStateDto": runner(LinearStateDto.self),
        // ClaudeOptionsModel.swift
        "ClaudeOptionsModel": runner(ClaudeOptionsModel.self),
        // CodexLoginDto.swift
        "CodexLoginRequest": runner(CodexLoginRequest.self),
        "CodexLoginResponse": runner(CodexLoginResponse.self),
        // CodexOptionsModel.swift
        "CodexOptionsModel": runner(CodexOptionsModel.self),
        // CustomAgentTypeDto.swift
        "CustomAgentTypeDto": runner(CustomAgentTypeDto.self),
        "CreateCustomAgentTypeRequest": runner(CreateCustomAgentTypeRequest.self),
        "UpdateCustomAgentTypeRequest": runner(UpdateCustomAgentTypeRequest.self),
        "CustomTypeDeletedPayload": runner(CustomTypeDeletedPayload.self),
        // DeviceDto.swift
        "DeviceResponse": runner(DeviceResponse.self),
        "RegisterPushTokenRequest": runner(RegisterPushTokenRequest.self),
        // ForceUpdateResponse.swift
        "ForceUpdateResponse": runner(ForceUpdateResponse.self),
        // GitDto.swift
        "GitItem": runner(GitItem.self),
        "GitItemsResponse": runner(GitItemsResponse.self),
        "GitDeleteItem": runner(GitDeleteItem.self),
        "GitDeleteRequest": runner(GitDeleteRequest.self),
        "GitDeleteResultItem": runner(GitDeleteResultItem.self),
        "GitDeleteResponse": runner(GitDeleteResponse.self),
        "BusyHolder": runner(BusyHolder.self),
        "BusyByResponse": runner(BusyByResponse.self),
        // PairingDtos.swift
        "PairingClaimRequest": runner(PairingClaimRequest.self),
        "PairingClaimResponse": runner(PairingClaimResponse.self),
        // ProjectInfo.swift
        "ProjectInfo": runner(ProjectInfo.self),
        // ProjectOrderRequest.swift
        "ProjectOrderRequest": runner(ProjectOrderRequest.self),
        // ProjectUpdate.swift
        "ProjectUpdate": runner(ProjectUpdate.self),
        // ProjectWrite.swift
        "ProjectWrite": runner(ProjectWrite.self),
        // SessionDto.swift
        "SessionKind": runner(SessionKind.self),
        "SessionResponse": runner(SessionResponse.self),
        "CreateSessionRequest": runner(CreateSessionRequest.self),
        "ScrollbackResponse": runner(ScrollbackResponse.self),
        // ShortcutDto.swift
        "ShortcutDto": runner(ShortcutDto.self),
        "CreateShortcutRequest": runner(CreateShortcutRequest.self),
        "UpdateShortcutRequest": runner(UpdateShortcutRequest.self),
        "ShortcutDeletedPayload": runner(ShortcutDeletedPayload.self),
        // TemplateDto.swift
        "TemplateDto": runner(TemplateDto.self),
        "CreateTemplateRequest": runner(CreateTemplateRequest.self),
        "UpdateTemplateRequest": runner(UpdateTemplateRequest.self),
        "TemplateDeletedPayload": runner(TemplateDeletedPayload.self),
        // VersionDto.swift
        "VersionInfoResponse": runner(VersionInfoResponse.self),
    ]
}
