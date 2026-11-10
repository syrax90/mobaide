import ImageIO
import UIKit

/// Every chat image decode goes through ImageIO: the pixel size is read from
/// the header first, so an oversized image is refused before any pixel is
/// decoded, and what is decoded is a thumbnail of bounded size.
enum ChatImageDecoder {
    enum Outcome: Sendable {
        case image(UIImage)
        case tooLarge
        case invalid
    }

    static let maxSourcePixels = 40_000_000
    static let maxDisplaySide = 2048

    static func decode(
        _ data: Data,
        maxPixelSize: Int = maxDisplaySide,
        maxSourcePixels: Int? = maxSourcePixels
    ) -> Outcome {
        let sourceOptions = [kCGImageSourceShouldCache: false] as CFDictionary
        guard let source = CGImageSourceCreateWithData(data as CFData, sourceOptions),
              CGImageSourceGetCount(source) > 0,
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
              let height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue,
              width > 0, height > 0
        else { return .invalid }
        if let maxSourcePixels {
            let (pixels, overflow) = width.multipliedReportingOverflow(by: height)
            if overflow || pixels > maxSourcePixels { return .tooLarge }
        }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
            return .invalid
        }
        return .image(UIImage(cgImage: image))
    }

    /// Decodes a base64 image part off the main actor.
    static func decode(base64: String, maxPixelSize: Int = maxDisplaySide) async -> Outcome {
        await Task.detached(priority: .userInitiated) {
            guard let data = Data(base64Encoded: base64) else { return Outcome.invalid }
            return decode(data, maxPixelSize: maxPixelSize)
        }.value
    }
}

/// Decoded agent images by owner, so a row scrolled back into view shows its
/// image at once instead of decoding it again.
@MainActor
enum ChatImageCache {
    private final class Entry {
        let outcome: ChatImageDecoder.Outcome
        init(_ outcome: ChatImageDecoder.Outcome) { self.outcome = outcome }
    }

    private static let storage: NSCache<NSString, Entry> = {
        let cache = NSCache<NSString, Entry>()
        cache.totalCostLimit = 96 * 1024 * 1024
        return cache
    }()

    static func outcome(for key: String) -> ChatImageDecoder.Outcome? {
        storage.object(forKey: key as NSString)?.outcome
    }

    static func store(_ outcome: ChatImageDecoder.Outcome, for key: String) {
        var cost = 1
        if case .image(let image) = outcome, let cgImage = image.cgImage {
            cost = cgImage.bytesPerRow * cgImage.height
        }
        storage.setObject(Entry(outcome), forKey: key as NSString, cost: cost)
    }
}
