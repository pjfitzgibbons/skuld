// Tests for SlotCore.swift, built by bin/test-slotbar with swiftc alone (no XCTest, no AppKit).
import Foundation

@main
struct SlotCoreTests {
    static var failures = 0

    static func check(_ condition: Bool, _ name: String) {
        print("\(condition ? "ok  " : "FAIL") \(name)")
        if !condition { failures += 1 }
    }

    static func main() {
        slotsParsing()
        missingSlotsFile()
        pulseValidation()
        pulseRoundTrip()
        shortcutParsing()
        print(failures == 0 ? "all passed" : "\(failures) failed")
        exit(failures == 0 ? 0 : 1)
    }

    static func slotsParsing() {
        check(parseSlots("3=SK-0045\n4=CIAM-12\n") == [Slot(desktop: 3, key: "SK-0045"), Slot(desktop: 4, key: "CIAM-12")],
              "parses N=KEY lines")
        check(parseSlots("3=SK-0045\r\n") == [Slot(desktop: 3, key: "SK-0045")], "accepts CRLF line ends")
        check(parseSlots(" 3 = SK-0045 ") == [Slot(desktop: 3, key: "SK-0045")], "trims spaces around the parts")
        check(parseSlots("x=SK-1\n0=SK-1\n-2=SK-1\n3=\n3\n=SK-1\n3=not a key\n").isEmpty, "skips malformed lines")
        check(parseSlots("# comment\n\n5=SK-7\n") == [Slot(desktop: 5, key: "SK-7")], "keeps good lines among bad")
        check(slot(on: 4, in: parseSlots("3=SK-1\n4=SK-2\n"))?.key == "SK-2", "finds the slot on a Desktop")
        check(slot(on: 2, in: parseSlots("3=SK-1\n")) == nil, "a Desktop with no slot has none")
    }

    static func missingSlotsFile() {
        check(readSlots(atPath: "/nonexistent/skuld/slots").isEmpty, "a missing slots file reads as no slots")
    }

    static func pulseValidation() {
        check(validatePulse(slot: "3", color: "red") == Pulse(desktop: 3, color: .red), "accepts a red pulse")
        check(validatePulse(slot: "16", color: "green") == Pulse(desktop: 16, color: .green), "accepts the top Desktop")
        check(validatePulse(slot: "1", color: "off") == Pulse(desktop: 1, color: .off), "accepts off")
        check(validatePulse(slot: "3", color: "blue") == nil, "rejects an unknown colour")
        check(validatePulse(slot: "3", color: "Red") == nil, "rejects a colour in another case")
        check(validatePulse(slot: "0", color: "red") == nil, "rejects Desktop 0")
        check(validatePulse(slot: "17", color: "red") == nil, "rejects a Desktop past 16")
        check(validatePulse(slot: "3x", color: "red") == nil, "rejects a slot that isn't a number")
        check(validatePulse(slot: 3, color: "red") == nil, "rejects a slot that isn't a string")
        check(validatePulse(slot: nil, color: "red") == nil, "rejects a missing slot")
        check(validatePulse(slot: "3", color: nil) == nil, "rejects a missing colour")
    }

    static func pulseRoundTrip() {
        let info = pulseUserInfo(Pulse(desktop: 4, color: .green))
        check(validatePulse(slot: info["slot"], color: info["color"]) == Pulse(desktop: 4, color: .green),
              "a posted pulse validates on arrival")
    }

    static func hotkey(enabled: Bool, keyCode: Int, mask: Int) -> [String: Any] {
        ["enabled": enabled, "value": ["type": "standard", "parameters": [65535, keyCode, mask]]]
    }

    static func shortcutParsing() {
        let domain: [String: Any] = ["AppleSymbolicHotKeys": [
            "118": hotkey(enabled: true, keyCode: 18, mask: 262144),
            "119": hotkey(enabled: false, keyCode: 19, mask: 262144),
            "121": hotkey(enabled: true, keyCode: 21, mask: 262144 | 524288),
            "133": hotkey(enabled: true, keyCode: 90, mask: 262144),
            "134": hotkey(enabled: true, keyCode: 91, mask: 262144),
            "60": hotkey(enabled: true, keyCode: 49, mask: 262144),
        ]]
        let shortcuts = parseDesktopShortcuts(domain)
        check(shortcuts[1] == DesktopShortcut(keyCode: 18, modifierMask: 262144), "reads Desktop 1's ^1")
        check(shortcuts[2] == nil, "a shortcut turned off is absent")
        check(shortcuts[3] == nil, "a shortcut never set is absent")
        check(shortcuts[4] == DesktopShortcut(keyCode: 21, modifierMask: 786432), "keeps every modifier")
        check(shortcuts[16]?.keyCode == 90, "reads Desktop 16")
        check(shortcuts.count == 3, "ignores hotkeys that aren't Desktop switches")
        check(parseDesktopShortcuts([:]).isEmpty, "an empty domain has no shortcuts")
        check(enabledShortcut(["enabled": true, "value": ["parameters": [65535, 18]]]) == nil,
              "rejects a malformed entry")
    }
}
