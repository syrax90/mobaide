import UIKit
import XCTest
@testable import Mobaide

/// The size bounds on chat images: the decoder refuses an image whose header
/// is over the pixel budget before it decodes a pixel, and the encoder fits a
/// picked image into the per-image base64 cap or gives up.
@MainActor
final class ChatImageCodecTests: XCTestCase {
    /// A PNG of random pixels: JPEG cannot compress it much, so the encoder
    /// has to work for its cap.
    private func noisePNG(width: Int, height: Int) throws -> Data {
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        bytes.withUnsafeMutableBytes { arc4random_buf($0.baseAddress, $0.count) }
        let provider = try XCTUnwrap(CGDataProvider(data: Data(bytes) as CFData))
        let image = try XCTUnwrap(CGImage(
            width: width,
            height: height,
            bitsPerComponent: 8,
            bitsPerPixel: 32,
            bytesPerRow: width * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
            provider: provider,
            decode: nil,
            shouldInterpolate: false,
            intent: .defaultIntent
        ))
        return try XCTUnwrap(UIImage(cgImage: image).pngData())
    }

    private func pixelSize(_ outcome: ChatImageDecoder.Outcome) -> CGSize? {
        guard case .image(let image) = outcome, let cgImage = image.cgImage else { return nil }
        return CGSize(width: cgImage.width, height: cgImage.height)
    }

    func testDecoderRefusesAnImageOverThePixelBudget() throws {
        let data = try noisePNG(width: 20, height: 20)

        guard case .tooLarge = ChatImageDecoder.decode(data, maxSourcePixels: 399) else {
            return XCTFail("a 400 px image over a 399 px budget must be refused")
        }
        XCTAssertEqual(pixelSize(ChatImageDecoder.decode(data, maxSourcePixels: 400)), CGSize(width: 20, height: 20))
    }

    func testEncoderFitsTheImageUnderTheCap() throws {
        let data = try noisePNG(width: 2400, height: 1200)
        let cap = ChatSessionModel.maxImageBase64

        let prepared = try XCTUnwrap(ChatImageEncoder.prepare(data, maxBase64: cap))

        XCTAssertEqual(prepared.payload.mime, "image/jpeg")
        XCTAssertLessThanOrEqual(prepared.payload.data.utf8.count, cap)
        let jpeg = try XCTUnwrap(Data(base64Encoded: prepared.payload.data))
        let size = try XCTUnwrap(pixelSize(ChatImageDecoder.decode(jpeg, maxPixelSize: 4096)))
        XCTAssertEqual(size, CGSize(width: ChatImageEncoder.maxSide, height: ChatImageEncoder.maxSide / 2))
    }

    func testEncoderGivesUpWhenNoQualityFitsTheCap() throws {
        let data = try noisePNG(width: 400, height: 400)

        XCTAssertNil(ChatImageEncoder.prepare(data, maxBase64: 2_000))
    }
}
