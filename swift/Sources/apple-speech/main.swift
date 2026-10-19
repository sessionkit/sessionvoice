import Foundation
import Speech
import AVFoundation
import Synchronization

func err(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }
func now() -> Double { Date().timeIntervalSince1970 }
func j(_ o: Any) -> String { String(data: try! JSONSerialization.data(withJSONObject: o, options: [.sortedKeys]), encoding: .utf8)! }

let args = Array(CommandLine.arguments.dropFirst())
let cmd = args.first ?? "help"
let locale = Locale(identifier: ProcessInfo.processInfo.environment["APPLE_SPEECH_LOCALE"] ?? "en-AU")

func locales() async {
  print("SpeechTranscriber.isAvailable:", SpeechTranscriber.isAvailable)
  let sup = await SpeechTranscriber.supportedLocales.map(\.identifier).sorted()
  let inst = await SpeechTranscriber.installedLocales.map(\.identifier).sorted()
  print("SpeechTranscriber supported (\(sup.count)):", sup.joined(separator: " "))
  print("SpeechTranscriber installed (\(inst.count)):", inst.joined(separator: " "))
  let dsup = await DictationTranscriber.supportedLocales.count
  let dinst = await DictationTranscriber.installedLocales.map(\.identifier).sorted()
  print("DictationTranscriber supported: \(dsup), installed:", dinst.joined(separator: " "))
  print("equivalent to \(locale.identifier):", await SpeechTranscriber.supportedLocale(equivalentTo: locale)?.identifier ?? "nil")
  let t = SpeechTranscriber(locale: locale, preset: .transcription)
  print("status [transcriber]:", await AssetInventory.status(forModules: [t]))
  print("status [detector]:", await AssetInventory.status(forModules: [SpeechDetector()]))
  print("reserved:", await AssetInventory.reservedLocales.map(\.identifier), "max:", AssetInventory.maximumReservedLocales)
}

// Every failure is ONE stderr line naming the locale, the step and the reason, then a
// non-zero exit: 2 = locale unsupported, 3 = the asset request or download failed.
// `ears install` shows that line; progress goes to stdout so it does not crowd it out.
func install() async throws {
  let id = locale.identifier
  guard await SpeechTranscriber.supportedLocale(equivalentTo: locale) != nil else {
    err("apple-speech: locale \(id) is not supported by SpeechTranscriber on this Mac (`apple-speech locales` lists the supported ones)")
    exit(2)
  }
  let mods: [any SpeechModule] = [SpeechTranscriber(locale: locale, preset: .transcription), SpeechDetector()]
  let t0 = now()
  var step = "asking for assets for \(id)"
  do {
    if let req = try await AssetInventory.assetInstallationRequest(supporting: mods) {
      step = "downloading assets for \(id)"
      print("\(step)..."); fflush(stdout)
      try await req.downloadAndInstall()
      print("installed in \(String(format: "%.1f", now()-t0))s")
    } else { print("already installed") }
  } catch {
    err("apple-speech: \(step) failed: \(error.localizedDescription); check the network and free disk space, then run: ears install --apple --locale \(id)")
    exit(3)
  }
}

// Words the transcriber should expect (names, jargon): APPLE_SPEECH_CONTEXT, comma-separated,
// and/or APPLE_SPEECH_CONTEXT_FILE, one per line. Tonight's calls (2030-06-09) lost exactly
// these: ears -> "beers", Talk -> "tour", pager -> "page", Claude -> "clawed".
func contextWords() -> [String] {
  var w: [String] = []
  if let s = ProcessInfo.processInfo.environment["APPLE_SPEECH_CONTEXT"] {
    w += s.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
  }
  if let p = ProcessInfo.processInfo.environment["APPLE_SPEECH_CONTEXT_FILE"],
     let s = try? String(contentsOfFile: p, encoding: .utf8) {
    w += s.split(separator: "\n").map { $0.split(separator: "#", maxSplits: 1).first.map(String.init) ?? "" }
          .map { $0.trimmingCharacters(in: .whitespaces) }
  }
  return w.filter { !$0.isEmpty }
}

func applyContext(_ analyzer: SpeechAnalyzer) async throws {
  let w = contextWords()
  if w.isEmpty { return }
  let ctx = AnalysisContext()
  ctx.contextualStrings[.general] = w
  try await analyzer.setContext(ctx)
}

func dictationFile(_ path: String) async throws {
  let t0 = now()
  let dt = DictationTranscriber(locale: locale, preset: .longDictation)
  let f = try AVAudioFile(forReading: URL(fileURLWithPath: path))
  let dur = Double(f.length) / f.fileFormat.sampleRate
  let analyzer = SpeechAnalyzer(modules: [dt], options: .init(priority: .userInitiated, modelRetention: .processLifetime))
  try await applyContext(analyzer)
  var text = ""
  let collect = Task { for try await r in dt.results { if r.isFinal { text += String(r.text.characters) } } }
  if let last = try await analyzer.analyzeSequence(from: f) {
    try await analyzer.finalizeAndFinish(through: last)
  } else { await analyzer.cancelAndFinishNow() }
  try await collect.value
  let wall = now() - t0
  print(j(["file": path, "audio_s": dur, "wall_s": wall, "rtf": wall/dur, "module": "dictation",
           "locale": locale.identifier, "text": text.trimmingCharacters(in: .whitespaces)]))
}

// Transcribe a file. Modes: final-only (default) or progressive (volatile results).
func file(_ path: String, progressive: Bool, withDetector: Bool) async throws {
  let t0 = now()
  let preset: SpeechTranscriber.Preset = progressive ? .timeIndexedProgressiveTranscription : .transcription
  let tr = SpeechTranscriber(locale: locale, preset: preset)
  // APPLE_SPEECH_MODULE=dictation: DictationTranscriber instead (an experiment, 2030-06-09:
  // does it honour contextual strings where SpeechTranscriber measured no change?)
  if ProcessInfo.processInfo.environment["APPLE_SPEECH_MODULE"] == "dictation" {
    try await dictationFile(path); return
  }
  var mods: [any SpeechModule] = [tr]
  let det = SpeechDetector(detectionOptions: .init(sensitivityLevel: .medium), reportResults: true)
  if withDetector { mods.append(det) }
  let f = try AVAudioFile(forReading: URL(fileURLWithPath: path))
  let dur = Double(f.length) / f.fileFormat.sampleRate
  let analyzer = SpeechAnalyzer(modules: mods, options: .init(priority: .userInitiated, modelRetention: .processLifetime))
  try await applyContext(analyzer)
  var firstVolatile: Double? = nil
  var text = ""
  var nVolatile = 0
  let collect = Task {
    for try await r in tr.results {
      if r.isFinal { text += String(r.text.characters) }
      else { nVolatile += 1; if firstVolatile == nil { firstVolatile = now() - t0 } }
    }
  }
  var speechSegs: [[Double]] = []
  let dcollect: Task<Void, Error>? = withDetector ? Task {
    for try await r in det.results {
      speechSegs.append([r.range.start.seconds, r.range.end.seconds, r.speechDetected ? 1 : 0, r.isFinal ? 1 : 0])
    }
  } : nil
  let tPrep = now()
  if let last = try await analyzer.analyzeSequence(from: f) {
    try await analyzer.finalizeAndFinish(through: last)
  } else { await analyzer.cancelAndFinishNow() }
  try await collect.value
  if let d = dcollect { try await d.value }
  let wall = now() - t0
  var out: [String: Any] = ["file": path, "audio_s": dur, "wall_s": wall, "rtf": wall/dur,
    "setup_s": tPrep - t0, "locale": locale.identifier, "text": text.trimmingCharacters(in: .whitespaces)]
  if progressive { out["volatile_results"] = nVolatile; out["first_volatile_s"] = firstVolatile ?? -1 }
  if withDetector { out["speech_segments"] = speechSegs }
  print(j(out))
}


func emit(_ o: [String: Any]) { print(j(o)); fflush(stdout) }

// One analyzer's worth of streaming. A COMMIT ends a session and starts a fresh
// one: finalize() on a live analyzer leaves it damaged for what follows - dots,
// dropped words, for the whole NEXT turn (a bot replay: "she...
// she, to., a." for 18 s; the eval saw the same at every-pause finalize). So a
// commit finishes the old analyzer and all later audio goes to a new one.
final class Session {
  let tr: SpeechTranscriber, analyzer: SpeechAnalyzer, conv: AnalyzerInputConverter
  let cont: AsyncStream<AnalyzerInput>.Continuation
  var reader: Task<Void, Error>? = nil
  init(tr: SpeechTranscriber, analyzer: SpeechAnalyzer, conv: AnalyzerInputConverter, cont: AsyncStream<AnalyzerInput>.Continuation) {
    self.tr = tr; self.analyzer = analyzer; self.conv = conv; self.cont = cont
  }
}

var seg = 1          // ears events v1: partials carry the open segment; each final closes it
let t0s = now()
let commitFlag = Atomic<Bool>(false)

// `after`: the previous session's reader. This session's results are relayed only
// once it has drained, so the old session's finals keep the lower seg numbers.
func makeSession(after prev: Task<Void, Error>?) async throws -> Session {
  let tr = SpeechTranscriber(locale: locale, preset: .timeIndexedProgressiveTranscription)
  let mods: [any SpeechModule] = [tr]
  let conv = try await AnalyzerInputConverter.converter(compatibleWith: mods)
  let analyzer = SpeechAnalyzer(modules: mods, options: .init(priority: .userInitiated, modelRetention: .processLifetime))
  try await applyContext(analyzer)
  try await analyzer.prepareToAnalyze(in: nil)
  let (inputs, cont) = AsyncStream.makeStream(of: AnalyzerInput.self)
  try await analyzer.start(inputSequence: inputs)
  let s = Session(tr: tr, analyzer: analyzer, conv: conv, cont: cont)
  s.reader = Task {
    if let p = prev { _ = try? await p.value }
    for try await r in tr.results {
      let text = String(r.text.characters).trimmingCharacters(in: .whitespaces)
      if r.isFinal {
        var words: [[Any]] = []
        for run in r.text.runs {
          if let wr = run.audioTimeRange {
            let w = String(r.text[run.range].characters).trimmingCharacters(in: .whitespaces)
            if !w.isEmpty { words.append([w, wr.start.seconds, wr.end.seconds]) }
          }
        }
        emit(["type": "final", "seg": seg, "text": text, "start": r.range.start.seconds, "end": r.range.end.seconds, "words": words, "t": now() - t0s])
        seg += 1
      } else {
        emit(["type": "partial", "seg": seg, "text": text, "start": r.range.start.seconds, "end": r.range.end.seconds, "t": now() - t0s])
      }
    }
  }
  return s
}

func finish(_ s: Session) async throws {
  for i in try s.conv.flush() { s.cont.yield(i) }
  s.cont.finish()
  try await s.analyzer.finalizeAndFinishThroughEndOfInput()
}

// stream: raw PCM s16le mono on stdin (rate from --rate, default 16000) -> JSON lines on stdout.
// events: ears events v1 (docs/events.md): ready, partial {seg,text,start,end}, final {seg,text,start,end,words:[[w,s,e],...]}, error, eof
// SIGUSR1 = commit: finalize everything fed so far NOW (and start a fresh analyzer).
// The turn layer sends it when it has decided the turn is over - Apple otherwise
// holds the last phrase before a silence open until the user speaks again (measured
// 2030-06-09: 4 s of quiet and no final).
func stream(rate: Double) async throws {
  var cur = try await makeSession(after: nil)
  signal(SIGUSR1, SIG_IGN)
  let sig = DispatchSource.makeSignalSource(signal: SIGUSR1, queue: .global())
  sig.setEventHandler { commitFlag.store(true, ordering: .relaxed) }
  sig.resume()
  emit(["type": "ready", "v": 1, "backend": "apple", "model": "SpeechTranscriber", "load_s": now() - t0s, "locale": locale.identifier, "rate": rate])
  let fmt = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: rate, channels: 1, interleaved: true)!
  let stdin = FileHandle.standardInput
  var pos: AVAudioFramePosition = 0
  var finishing: [Task<Void, Error>] = []
  while true {
    let data = stdin.availableData   // blocks until some bytes or EOF
    if data.isEmpty { break }
    if commitFlag.exchange(false, ordering: .relaxed) {
      let old = cur
      finishing.append(Task { try await finish(old) })
      cur = try await makeSession(after: old.reader)
    }
    let frames = AVAudioFrameCount(data.count / 2)
    if frames == 0 { continue }
    let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: frames)!
    buf.frameLength = frames
    data.withUnsafeBytes { raw in memcpy(buf.int16ChannelData![0], raw.baseAddress!, Int(frames) * 2) }
    for i in try cur.conv.convert(buf, at: AVAudioTime(sampleTime: pos, atRate: rate)) { cur.cont.yield(i) }
    pos += AVAudioFramePosition(frames)
  }
  try await finish(cur)
  for f in finishing { _ = try? await f.value }
  _ = try await cur.reader?.value
  emit(["type": "eof", "audio_s": Double(pos) / rate, "t": now() - t0s])
}

do {
  switch cmd {
  case "locales": await locales()
  case "install": try await install()
  case "stream":
    let rate = args.firstIndex(of: "--rate").map { Double(args[$0 + 1])! } ?? 16000
    try await stream(rate: rate)
  case "file":
    for p in args.dropFirst() where !p.hasPrefix("-") {
      try await file(p, progressive: args.contains("--progressive"), withDetector: args.contains("--vad"))
    }
  default: err("usage: apple-speech locales | install | file PATH... [--progressive] [--vad] | stream [--rate HZ]  (s16le mono on stdin, JSON lines out)")
  }
} catch {
  err("error: \(error)")
  if cmd == "stream" { emit(["type": "error", "message": "\(error)", "fatal": true]); emit(["type": "eof", "audio_s": 0]) }
  exit(1)
}
