// multi-output.swift - make (or list) a macOS Multi-Output Device from the CLI,
// the same thing Audio MIDI Setup's "+ > Create Multi-Output Device" makes.
//
// A Delta call from
// the Mac plays to the user's AirPods AND BlackHole 2ch, so the ears capture BlackHole as
// the far end. Detail: docs/call-tap.md.
//
//   swift bin/multi-output.swift list
//   swift bin/multi-output.swift make "AirPods + BlackHole" airpods "BlackHole 2ch"
//
// The FIRST device is the main (clock) device; every other one gets drift
// correction. The device is public (not private), so it outlives this process.
import CoreAudio
import Foundation

func prop<T>(_ id: AudioObjectID, _ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal, _ empty: T) -> T? {
    var addr = AudioObjectPropertyAddress(mSelector: sel, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    var value = empty
    let st = withUnsafeMutablePointer(to: &value) { AudioObjectGetPropertyData(id, &addr, 0, nil, &size, $0) }
    return st == noErr ? value : nil
}

func devices() -> [AudioObjectID] {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size) == noErr else { return [] }
    var ids = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func name(_ id: AudioObjectID) -> String {
    (prop(id, kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal, "" as CFString) as String?) ?? "?"
}
func uid(_ id: AudioObjectID) -> String {
    (prop(id, kAudioDevicePropertyDeviceUID, kAudioObjectPropertyScopeGlobal, "" as CFString) as String?) ?? "?"
}
func outChannels(_ id: AudioObjectID) -> Int {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration, mScope: kAudioObjectPropertyScopeOutput, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr, size > 0 else { return 0 }
    let buf = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
    defer { buf.deallocate() }
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, buf) == noErr else { return 0 }
    let list = UnsafeMutableAudioBufferListPointer(buf.assumingMemoryBound(to: AudioBufferList.self))
    return list.reduce(0) { $0 + Int($1.mNumberChannels) }
}

let args = CommandLine.arguments
if args.count >= 2 && args[1] == "list" {
    for d in devices() where outChannels(d) > 0 { print("\(name(d))\t\(uid(d))\tout=\(outChannels(d))") }
    exit(0)
}
guard args.count >= 5 && args[1] == "make" else {
    FileHandle.standardError.write("usage: multi-output.swift list | make NAME MAIN_DEVICE OTHER_DEVICE...\n".data(using: .utf8)!)
    exit(1)
}
let newName = args[2]
if let clash = devices().first(where: { name($0) == newName }) {
    FileHandle.standardError.write("refused: a device named \(newName) already exists (uid \(uid(clash)))\n".data(using: .utf8)!)
    exit(2)
}
var subs: [[String: Any]] = []
var mainUID = ""
for (i, want) in args[3...].enumerated() {
    guard let d = devices().first(where: { name($0) == want && outChannels($0) > 0 }) else {
        FileHandle.standardError.write("refused: no output device named exactly \(want)\n".data(using: .utf8)!)
        exit(2)
    }
    var sub: [String: Any] = [kAudioSubDeviceUIDKey: uid(d)]
    if i == 0 { mainUID = uid(d) } else { sub[kAudioSubDeviceDriftCompensationKey] = 1 }
    subs.append(sub)
}
let desc: [String: Any] = [
    kAudioAggregateDeviceNameKey: newName,
    kAudioAggregateDeviceUIDKey: "dev.sessionkit.voice.multi-output." + newName.lowercased().replacingOccurrences(of: " ", with: "-"),
    kAudioAggregateDeviceSubDeviceListKey: subs,
    kAudioAggregateDeviceMainSubDeviceKey: mainUID,
    kAudioAggregateDeviceIsStackedKey: 1,      // stacked = Multi-Output (every sub plays the same audio)
    kAudioAggregateDeviceIsPrivateKey: 0,      // public: survives this process
]
var newID = AudioObjectID(0)
let st = AudioHardwareCreateAggregateDevice(desc as CFDictionary, &newID)
guard st == noErr else {
    FileHandle.standardError.write("AudioHardwareCreateAggregateDevice failed: OSStatus \(st)\n".data(using: .utf8)!)
    exit(3)
}
print("made \(newName) (id \(newID), uid \(uid(newID))): main \(args[3]), plus \(args[4...].joined(separator: ", "))")
