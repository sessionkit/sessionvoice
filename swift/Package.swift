// swift-tools-version: 6.2
// ears' Mac helper: the Speech framework (SpeechAnalyzer / SpeechTranscriber) as a
// process that reads s16le PCM on stdin and writes ears events (docs/events.md) as
// JSON lines on stdout. A process, not in-process bindings: Speech TRAPS on some
// bad input (SIGTRAP in AnalyzerInput.data(from:), measured 2030-06-09), and a trap
// in a helper kills the helper, not the capture.
import PackageDescription
let package = Package(
  name: "apple-speech",
  platforms: [.macOS("27.0")],
  targets: [.executableTarget(name: "apple-speech", swiftSettings: [.swiftLanguageMode(.v5)])]
)
