import SwiftUI

/// Web `.theme-seg`: ringed segments overlapping by 2pt so neighbours share
/// one divider; the selected one sits on `surface` inside a `brand2` ring.
struct PixelSegmented<Value: Hashable>: View {
    struct Segment: Identifiable {
        let value: Value
        let label: String

        var id: Value { value }
    }

    let segments: [Segment]
    @Binding var selection: Value

    @Environment(\.mobaideColors) private var colors

    init(_ segments: [(Value, String)], selection: Binding<Value>) {
        self.segments = segments.map { Segment(value: $0.0, label: $0.1) }
        _selection = selection
    }

    var body: some View {
        HStack(spacing: -PixelMetrics.ring) {
            ForEach(segments) { segment in
                let selected = segment.value == selection
                Button {
                    selection = segment.value
                } label: {
                    Text(segment.label)
                        .chromeText()
                        .foregroundStyle(selected ? colors.text : colors.textMuted)
                        .lineLimit(1)
                        .padding(EdgeInsets(top: 7, leading: 11, bottom: 7, trailing: 11))
                        .frame(maxWidth: .infinity)
                        .background(selected ? colors.surface : Color.clear)
                        .pixelRing(selected ? MobaideColorScheme.brand2 : colors.gray)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .zIndex(selected ? 1 : 0)
            }
        }
        .transaction { $0.animation = nil }
        .accessibilityRepresentation {
            Picker("", selection: $selection) {
                ForEach(segments) { segment in
                    Text(segment.label).tag(segment.value)
                }
            }
            .pickerStyle(.segmented)
        }
    }
}
