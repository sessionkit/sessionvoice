// input-watch: the Mac's audio devices, inputs AND outputs, written to state
// files whenever CoreAudio says something changed. One long-lived CoreAudio
// client instead of a new one for every question.
//
// Why (a coreaudiod finding):
// ears-keeper ran `system_profiler SPAudioDataType` every second. Measured
// against coreaudiod, 25 s windows, twice: base 41%, plus one more
// system_profiler a second +17 points, SwitchAudioSource -a +9, -c +7. Every
// new process that opens CoreAudio costs coreaudiod; a listener costs ~0.
// (Not an agent's own audio-watch, which pages an agent on device changes.)
//
// Outputs: the keeper's output check still ran
// SwitchAudioSource twice a check. This watcher now also hears the default
// output and writes one JSON state file, the contract in docs/audio-devices.md
// (schema: docs/audio-devices.schema.json), so readers stop spawning.
//
// Usage:
//   input-watch [STATE_FILE] [--json JSON_FILE]
//       The daemon. Listens on the device list, the default input and the
//       default output; re-reads every 30 s as a backstop.
//   input-watch --once --json JSON_FILE [STATE_FILE]
//       One read-only CoreAudio read: write, exit. No listener.
//   input-watch --fixture STEPS.json --json JSON_FILE [STATE_FILE]
//       Tests: no CoreAudio at all. STEPS.json is an array; each step is a
//       snapshot ({"default_input", "default_output", "inputs", "outputs"},
//       devices as in the JSON file), {"reassert": true} (the backstop's
//       own-file check), or {"foreign": OBJECT|null} (another writer replaces
//       the JSON file with OBJECT, or deletes it).
//
//   STATE_FILE (legacy, the keeper's input path): line 1 = the default input's
//   name, then one input device name per line, sorted and deduped.
//   JSON_FILE: docs/audio-devices.md. Both are rewritten atomically (temp file
//   + rename), each only when ITS content changes, JSON first. Each write
//   prints one line to stdout.
//
//   --once/--fixture REFUSE (exit 3, nothing written) a JSON_FILE whose pid is
//   alive and not theirs: a stray writer must not replace the daemon's file.
//   The daemon's backstop re-asserts its file when the on-disk pid is not its
//   own (one file read; a write only then). Exit 2 = usage, 1 = a listener or
//   a write failed.
import CoreAudio
import Foundation

let system = AudioObjectID(kAudioObjectSystemObject)
let me = getpid()
let startedAt = ms(Date().timeIntervalSince1970)

// seconds since the epoch, exactly 3 decimals in the JSON (a Double prints as 1.8829999)
func ms(_ t: Double) -> NSDecimalNumber { NSDecimalNumber(string: String(format: "%.3f", t)) }

func note(_ s: String) {
    FileHandle.standardError.write("input-watch: \(s)\n".data(using: .utf8)!)
}

// MARK: arguments

var statePath: String?
var jsonPath: String?
var fixturePath: String?
var once = false

func usage(_ why: String) -> Never {
    note(why)
    note("usage: input-watch [STATE_FILE] [--json JSON_FILE] | --once --json JSON_FILE [STATE_FILE]"
         + " | --fixture STEPS.json --json JSON_FILE [STATE_FILE]")
    exit(2)
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    var i = 0
    var positional: [String] = []
    func value(_ flag: String) -> String {
        i += 1
        guard i < args.count else { usage("\(flag) needs a value") }
        return args[i]
    }
    while i < args.count {
        switch args[i] {
        case "--json": jsonPath = value("--json")
        case "--fixture": fixturePath = value("--fixture")
        case "--once": once = true
        case let a where a.hasPrefix("--"): usage("unknown option \(a)")
        case let a: positional.append(a)
        }
        i += 1
    }
    if positional.count > 1 { usage("one STATE_FILE at most") }
    statePath = positional.first
    if once && fixturePath != nil { usage("--once and --fixture are two modes; pick one") }
    if (once || fixturePath != nil) && jsonPath == nil { usage("--once and --fixture need --json") }
}

// MARK: the model

struct Device {
    let name: String
    let uid: String?
    let transport: String
    var json: [String: Any] { ["name": name, "uid": uid ?? NSNull(), "transport": transport] }
}

func byName(_ a: Device, _ b: Device) -> Bool { (a.name, a.uid ?? "") < (b.name, b.uid ?? "") }

struct Snapshot {
    let defaultInput: Device?
    let defaultOutput: Device?
    let inputs: [Device]
    let outputs: [Device]
    init(defaultInput: Device?, defaultOutput: Device?, inputs: [Device], outputs: [Device]) {
        self.defaultInput = defaultInput
        self.defaultOutput = defaultOutput
        self.inputs = inputs.sorted(by: byName)
        self.outputs = outputs.sorted(by: byName)
    }
    // what a change is: the stamps (pid, seq, times) are not part of it
    var content: [String: Any] {
        ["default_input": defaultInput?.json ?? NSNull(),
         "default_output": defaultOutput?.json ?? NSNull(),
         "inputs": inputs.map(\.json),
         "outputs": outputs.map(\.json)]
    }
    // the legacy STATE_FILE, byte-for-byte as before outputs were added
    var legacy: String {
        ([defaultInput?.name ?? ""] + Set(inputs.map(\.name)).sorted()).joined(separator: "\n") + "\n"
    }
}

// MARK: CoreAudio, read-only: the one place that asks it anything

func addr(_ sel: AudioObjectPropertySelector,
          _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}

func deviceIDs() -> [AudioDeviceID] {
    var a = addr(kAudioHardwarePropertyDevices)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(system, &a, 0, nil, &size) == noErr, size > 0 else { return [] }
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(system, &a, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func string(_ id: AudioObjectID, _ sel: AudioObjectPropertySelector) -> String? {
    var a = addr(sel)
    var cf: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, &cf) == noErr, let s = cf else { return nil }
    return s.takeRetainedValue() as String
}

func hasStreams(_ id: AudioDeviceID, _ scope: AudioObjectPropertyScope) -> Bool {
    var a = addr(kAudioDevicePropertyStreams, scope)
    var size: UInt32 = 0
    return AudioObjectGetPropertyDataSize(id, &a, 0, nil, &size) == noErr && size > 0
}

let transports: [UInt32: String] = [
    kAudioDeviceTransportTypeBuiltIn: "builtin",
    kAudioDeviceTransportTypeBluetooth: "bluetooth",
    kAudioDeviceTransportTypeBluetoothLE: "bluetooth",
    kAudioDeviceTransportTypeUSB: "usb",
    kAudioDeviceTransportTypeVirtual: "virtual",
    kAudioDeviceTransportTypeAggregate: "aggregate",
    kAudioDeviceTransportTypeAutoAggregate: "aggregate",
    kAudioDeviceTransportTypeHDMI: "hdmi",
    kAudioDeviceTransportTypeDisplayPort: "displayport",
    kAudioDeviceTransportTypeAirPlay: "airplay",
    kAudioDeviceTransportTypeThunderbolt: "thunderbolt",
    kAudioDeviceTransportTypePCI: "pci",
    kAudioDeviceTransportTypeFireWire: "firewire",
    kAudioDeviceTransportTypeAVB: "avb",
    kAudioDeviceTransportTypeContinuityCaptureWired: "continuity",
    kAudioDeviceTransportTypeContinuityCaptureWireless: "continuity",
]

func transport(_ id: AudioDeviceID) -> String {
    var a = addr(kAudioDevicePropertyTransportType)
    var t: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, &t) == noErr else { return "unknown" }
    return transports[t] ?? "unknown"
}

func device(_ id: AudioDeviceID) -> Device? {
    guard let n = string(id, kAudioObjectPropertyName) else { return nil }
    return Device(name: n, uid: string(id, kAudioDevicePropertyDeviceUID), transport: transport(id))
}

func defaultDevice(_ sel: AudioObjectPropertySelector) -> Device? {
    var a = addr(sel)
    var id = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(system, &a, 0, nil, &size, &id) == noErr, id != 0 else { return nil }
    return device(id)
}

func snapshotNow() -> Snapshot {
    var ins: [Device] = [], outs: [Device] = []
    for id in deviceIDs() {
        guard let d = device(id) else { continue }
        if hasStreams(id, kAudioObjectPropertyScopeInput) { ins.append(d) }
        if hasStreams(id, kAudioObjectPropertyScopeOutput) { outs.append(d) }
    }
    return Snapshot(defaultInput: defaultDevice(kAudioHardwarePropertyDefaultInputDevice),
                    defaultOutput: defaultDevice(kAudioHardwarePropertyDefaultOutputDevice),
                    inputs: ins, outputs: outs)
}

// MARK: the writer: both files, atomically, each only on its own change

var failed = false        // a write failed (--once/--fixture exit 1)
var seq = 0               // writes of JSON_FILE by this process
var current: Snapshot?    // the last snapshot applied
var lastContent: String?  // the JSON content last written (stamps excluded)
var lastLegacy: String?

func encode(_ obj: Any) -> Data {
    var d = try! JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .withoutEscapingSlashes])
    d.append(0x0A)
    return d
}

func writeAtomically(_ data: Data, to path: String) -> Bool {
    let tmp = "\(path).tmp.\(me)"
    let fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0o644)
    guard fd >= 0 else { note("cannot write \(tmp): \(String(cString: strerror(errno)))"); failed = true; return false }
    fchmod(fd, 0o644)
    let ok = data.withUnsafeBytes { Darwin.write(fd, $0.baseAddress, data.count) == data.count }
    close(fd)
    if ok && rename(tmp, path) == 0 { return true }
    note("cannot write \(path): \(String(cString: strerror(errno)))")
    unlink(tmp)
    failed = true
    return false
}

func writeJSON(_ s: Snapshot, to path: String) -> Bool {
    var obj = s.content
    obj["version"] = 1
    obj["writer"] = "input-watch"
    obj["pid"] = Int(me)
    obj["started_at"] = startedAt
    obj["written_at"] = ms(Date().timeIntervalSince1970)
    obj["seq"] = seq + 1
    guard writeAtomically(encode(obj), to: path) else { return false }
    seq += 1
    return true
}

func say(_ s: Snapshot) {
    func names(_ ds: [Device]) -> String { ds.map(\.name).joined(separator: ", ") }
    print("default: \(s.defaultInput?.name ?? "") | inputs: \(Set(s.inputs.map(\.name)).sorted().joined(separator: ", "))"
          + " | default out: \(s.defaultOutput?.name ?? "") | outputs: \(names(s.outputs))")
    fflush(stdout)
}

func apply(_ s: Snapshot) {
    current = s
    var wrote = false
    if let path = jsonPath {
        let key = String(data: encode(s.content), encoding: .utf8)!
        if key != lastContent, writeJSON(s, to: path) { lastContent = key; wrote = true }
    }
    let text = s.legacy
    if text != lastLegacy {
        if let path = statePath {
            if writeAtomically(text.data(using: .utf8)!, to: path) { lastLegacy = text; wrote = true }
        } else {
            lastLegacy = text; wrote = true
        }
    }
    if wrote { say(s) }
}

// MARK: who owns JSON_FILE: the stray-writer guard and the re-assert

func filePid(_ path: String) -> pid_t? {
    guard let d = FileManager.default.contents(atPath: path),
          let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
          let n = o["pid"] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
          let p = Int32(exactly: n.doubleValue), p > 0 else { return nil }
    return p
}

func alive(_ pid: pid_t) -> Bool { kill(pid, 0) == 0 || errno == EPERM }

func refuseStray() {
    guard let path = jsonPath, let other = filePid(path), other != me, alive(other) else { return }
    note("REFUSED: \(path) is written by live pid \(other); a one-shot never replaces a live writer's file")
    exit(3)
}

func reassert() {
    guard let path = jsonPath, let s = current, lastContent != nil else { return }
    let onDisk = filePid(path)
    if onDisk == me { return }
    if let other = onDisk, alive(other) {
        note("\(path) names live pid \(other), not this one (\(me)); re-asserting it")
    }
    if writeJSON(s, to: path) { say(s) }
}

// MARK: the modes

func fixtureDevice(_ v: Any?, _ step: Int) -> Device? {
    if v == nil || v is NSNull { return nil }
    guard let o = v as? [String: Any], let n = o["name"] as? String, let t = o["transport"] as? String else {
        usage("fixture step \(step): a device needs a string name and transport")
    }
    if let u = o["uid"], !(u is NSNull || u is String) { usage("fixture step \(step): uid is a string or null") }
    return Device(name: n, uid: o["uid"] as? String, transport: t)
}

if let fixture = fixturePath {
    guard let d = FileManager.default.contents(atPath: fixture),
          let steps = try? JSONSerialization.jsonObject(with: d) as? [Any] else {
        usage("--fixture \(fixture): not a JSON array")
    }
    refuseStray()
    for (n, raw) in steps.enumerated() {
        guard let step = raw as? [String: Any] else { usage("fixture step \(n): not an object") }
        if step["reassert"] as? Bool == true {
            reassert()
        } else if step.keys.contains("foreign") {
            if let obj = step["foreign"], !(obj is NSNull) {
                _ = writeAtomically(encode(obj), to: jsonPath!)
            } else {
                unlink(jsonPath!)
            }
        } else {
            func list(_ k: String) -> [Device] {
                guard let a = step[k] as? [Any] else { usage("fixture step \(n): \(k) is a list") }
                return a.compactMap { fixtureDevice($0, n) }
            }
            apply(Snapshot(defaultInput: fixtureDevice(step["default_input"], n),
                           defaultOutput: fixtureDevice(step["default_output"], n),
                           inputs: list("inputs"), outputs: list("outputs")))
        }
    }
    exit(failed ? 1 : 0)
}

if once {
    refuseStray()
    apply(snapshotNow())
    exit(failed ? 1 : 0)
}

// The daemon. A burst of notifications (AirPods leaving: the list and both
// defaults within milliseconds) becomes ONE read 100 ms after the last of it.
var pending: DispatchWorkItem?
func soon() {
    pending?.cancel()
    let w = DispatchWorkItem { apply(snapshotNow()) }
    pending = w
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.1, execute: w)
}

for sel in [kAudioHardwarePropertyDevices, kAudioHardwarePropertyDefaultInputDevice,
            kAudioHardwarePropertyDefaultOutputDevice] {
    var a = addr(sel)
    let err = AudioObjectAddPropertyListenerBlock(system, &a, DispatchQueue.main) { _, _ in soon() }
    if err != noErr {
        note("listener \(sel) failed: \(err)")
        exit(1)
    }
}
apply(snapshotNow())
// The backstop: a missed notification is at most 30 s wrong, and a file some
// other writer replaced is ours again within 30 s. No spawn; writes only on change.
let backstop = Timer(timeInterval: 30, repeats: true) { _ in
    apply(snapshotNow())
    reassert()
}
RunLoop.main.add(backstop, forMode: .common)
RunLoop.main.run()
