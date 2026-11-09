// SlotBar's AppKit-free core: the slots file and the pulse message. Compiled into SlotBar, the
// pulse CLI and the test runner alike, so it imports Foundation only.
import Foundation

let pulseNotification = Notification.Name("skuld.pulse")
let slotBarBundleID = "dev.skuld.slotbar"

struct Slot: Equatable {
    let desktop: Int
    let key: String
}

// The slots file holds `<Desktop number>=<KEY>` lines, written by SpacesPlacer (SK-0044).
// A line that isn't that shape is skipped, so a half-written or foreign file shows fewer marks
// rather than none.
func parseSlots(_ text: String) -> [Slot] {
    text.split(whereSeparator: \.isNewline).compactMap(parseSlotLine)
}

func parseSlotLine<S: StringProtocol>(_ line: S) -> Slot? {
    let parts = line.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
    guard parts.count == 2, let desktop = Int(parts[0]), desktop >= 1, isIssueKey(parts[1]) else { return nil }
    return Slot(desktop: desktop, key: parts[1])
}

func isIssueKey(_ text: String) -> Bool {
    text.range(of: #"^[A-Z][A-Z0-9]*-[0-9]+$"#, options: .regularExpression) != nil
}

// A missing or unreadable slots file means no slots (L3): SlotBar still shows the Desktops.
func readSlots(atPath path: String) -> [Slot] {
    parseSlots((try? String(contentsOfFile: path, encoding: .utf8)) ?? "")
}

func slot(on desktop: Int, in slots: [Slot]) -> Slot? {
    slots.first { $0.desktop == desktop }
}

enum PulseColor: String {
    case red, green, off
}

struct Pulse: Equatable {
    let desktop: Int
    let color: PulseColor
}

// The highest Desktop number a pulse may name. macOS allows 16 Desktops per display.
let maxDesktop = 16

// A pulse arrives as a distributed notification any local process can post (L1). Only a Desktop
// number in range and one of the known colours get through; anything else is nil and ignored.
func validatePulse(slot: Any?, color: Any?) -> Pulse? {
    guard let slotText = slot as? String, let colorText = color as? String,
          let desktop = Int(slotText), (1...maxDesktop).contains(desktop),
          let pulseColor = PulseColor(rawValue: colorText) else { return nil }
    return Pulse(desktop: desktop, color: pulseColor)
}

func pulseUserInfo(_ pulse: Pulse) -> [String: String] {
    ["slot": String(pulse.desktop), "color": pulse.color.rawValue]
}

// A "Switch to Desktop N" keyboard shortcut. modifierMask uses the same bits as CGEventFlags
// (shift 0x20000, control 0x40000, option 0x80000, command 0x100000).
struct DesktopShortcut: Equatable {
    let keyCode: UInt16
    let modifierMask: UInt64
}

// System Settings stores "Switch to Desktop N" as symbolic hotkey 118 + N - 1, for N up to 16.
let firstDesktopHotkey = 118

// {Desktop number: shortcut} for each Desktop whose shortcut is turned on, read from the
// com.apple.symbolichotkeys domain. SK-0044's parseDesktopShortcuts reads the same entries.
func parseDesktopShortcuts(_ domain: [String: Any]) -> [Int: DesktopShortcut] {
    let hotkeys = domain["AppleSymbolicHotKeys"] as? [String: Any] ?? [:]
    var shortcuts: [Int: DesktopShortcut] = [:]
    for desktop in 1...maxDesktop {
        if let shortcut = enabledShortcut(hotkeys[String(firstDesktopHotkey + desktop - 1)]) {
            shortcuts[desktop] = shortcut
        }
    }
    return shortcuts
}

// An entry is {enabled, value: {parameters: [character, key code, modifier mask]}}.
func enabledShortcut(_ entry: Any?) -> DesktopShortcut? {
    guard let entry = entry as? [String: Any],
          (entry["enabled"] as? NSNumber)?.boolValue == true,
          let value = entry["value"] as? [String: Any],
          let parameters = value["parameters"] as? [NSNumber], parameters.count == 3 else { return nil }
    return DesktopShortcut(keyCode: parameters[1].uint16Value, modifierMask: parameters[2].uint64Value)
}

// How many Desktops there are and which are showing, one per display at most.
struct DesktopState: Equatable {
    let count: Int
    let showing: Set<Int>
}

// SkyLight's space type for a Desktop; a full-screen app's space has another type.
let desktopSpaceType = 0

// Numbers Desktops from CGSCopyManagedDisplaySpaces' output the way ^N and SK-0044's
// macos/spaces.swift count them: across every display in order, skipping full-screen spaces.
func desktopState(fromDisplays displays: [[String: Any]]) -> DesktopState {
    var count = 0
    var showing: Set<Int> = []
    for display in displays {
        let current = (display["Current Space"] as? [String: Any])?["ManagedSpaceID"] as? Int
        for space in display["Spaces"] as? [[String: Any]] ?? [] where space["type"] as? Int == desktopSpaceType {
            count += 1
            if let id = space["ManagedSpaceID"] as? Int, id == current { showing.insert(count) }
        }
    }
    return DesktopState(count: count, showing: showing)
}
