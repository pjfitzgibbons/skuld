// SlotBar: a menu-bar extra that shows each Desktop, marks issue slots, pulses a slot on request
// and switches to a Desktop chosen from its menu. Switching sends the Desktop's "Switch to Desktop
// N" shortcut, which needs the Accessibility grant; nothing else does.
// Usage: SlotBar <slots file>
//   slots file: `N=KEY` lines (SlotCore.swift).
import AppKit
import ApplicationServices

typealias Conn = Int32
@_silgen_name("CGSMainConnectionID") func CGSMainConnectionID() -> Conn
@_silgen_name("CGSCopyManagedDisplaySpaces") func CGSCopyManagedDisplaySpaces(_ cid: Conn) -> CFArray

struct DesktopState {
    let count: Int
    let showing: Int
}

// The first display's Desktops and which one is showing, through a read-only SkyLight call.
func readDesktops() -> DesktopState {
    let displays = CGSCopyManagedDisplaySpaces(CGSMainConnectionID()) as? [[String: Any]] ?? []
    guard let main = displays.first,
          let spaces = main["Spaces"] as? [[String: Any]] else { return DesktopState(count: 0, showing: 0) }
    let currentID = (main["Current Space"] as? [String: Any])?["ManagedSpaceID"] as? Int
    let showingIndex = spaces.firstIndex { $0["ManagedSpaceID"] as? Int == currentID } ?? -1
    return DesktopState(count: spaces.count, showing: showingIndex + 1)
}

struct LivePulse {
    let color: NSColor
    let started: Date
}

final class SlotBar: NSObject {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let slotsPath: String
    var slots: [Slot] = []
    var pulses: [Int: LivePulse] = [:]
    var lastPlacement = ""
    let fadeSeconds = 3.0
    let heldAlpha = 0.6

    init(slotsPath: String) {
        self.slotsPath = slotsPath
        super.init()
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(drawTitle), name: NSWorkspace.activeSpaceDidChangeNotification, object: nil)
        DistributedNotificationCenter.default().addObserver(
            self, selector: #selector(receivePulse(_:)), name: pulseNotification, object: nil)
        Timer.scheduledTimer(withTimeInterval: 1.0 / 20, repeats: true) { [weak self] _ in self?.animatePulses() }
        Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.reloadSlots() }
        reloadSlots()
    }

    // The 5 s timer is how slots-file changes reach the bar.
    func reloadSlots() {
        slots = readSlots(atPath: slotsPath)
        item.menu = menu(desktops: readDesktops().count, slots: slots, shortcuts: readDesktopShortcuts())
        drawTitle()
    }

    @objc func receivePulse(_ note: Notification) {
        guard let pulse = validatePulse(slot: note.userInfo?["slot"], color: note.userInfo?["color"]) else { return }
        switch pulse.color {
        case .red: pulses[pulse.desktop] = LivePulse(color: .systemRed, started: Date())
        case .green: pulses[pulse.desktop] = LivePulse(color: .systemGreen, started: Date())
        case .off: pulses[pulse.desktop] = nil
        }
        drawTitle()
    }

    func animatePulses() {
        if pulses.values.contains(where: isFading) { drawTitle() }
    }

    func isFading(_ pulse: LivePulse) -> Bool {
        Date().timeIntervalSince(pulse.started) < fadeSeconds
    }

    // A pulse fades in and out once a second for fadeSeconds, then holds a steady tint until the
    // next pulse or `off`.
    func tint(for desktop: Int) -> NSColor? {
        guard let pulse = pulses[desktop] else { return nil }
        guard isFading(pulse) else { return pulse.color.withAlphaComponent(heldAlpha) }
        let elapsed = Date().timeIntervalSince(pulse.started)
        return pulse.color.withAlphaComponent(abs(sin(elapsed * .pi * 2)))
    }

    // A slot is marked with a dot and the showing Desktop with an underline, so neither depends on
    // colour (L7). Issue keys stay in the menu to keep the item narrow.
    func segment(desktop: Int, hasSlot: Bool, showing: Bool) -> NSAttributedString {
        var attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.menuBarFont(ofSize: 0),
            .foregroundColor: showing ? NSColor.labelColor : NSColor.secondaryLabelColor,
        ]
        if showing { attrs[.underlineStyle] = NSUnderlineStyle.single.rawValue }
        if let tint = tint(for: desktop) { attrs[.backgroundColor] = tint }
        return NSAttributedString(string: hasSlot ? " \(desktop)• " : " \(desktop) ", attributes: attrs)
    }

    @objc func drawTitle() {
        let desktops = readDesktops()
        let title = NSMutableAttributedString()
        for desktop in 1...max(desktops.count, 1) {
            title.append(segment(desktop: desktop,
                                 hasSlot: slot(on: desktop, in: slots) != nil,
                                 showing: desktop == desktops.showing))
        }
        item.button?.attributedTitle = title
        reportPlacement()
    }

    // Prints where macOS put the item when that changes. A height of 0 means macOS hasn't placed
    // it in the menu bar; bin/install-slotbar reads this line from the log.
    func reportPlacement() {
        let window = item.button?.window
        let frame = window?.frame ?? .zero
        let line = "item frame=\(frame) placed=\(frame.height > 0) visible=\(window?.isVisible ?? false)"
        guard line != lastPlacement else { return }
        print(line)
        fflush(stdout)
        lastPlacement = line
    }

    // One entry per Desktop, `N  KEY` for a slot and `N` otherwise. A Desktop whose shortcut is
    // off in System Settings can't be reached, so its entry is disabled.
    func menu(desktops: Int, slots: [Slot], shortcuts: [Int: DesktopShortcut]) -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false
        for desktop in 1...max(desktops, 1) {
            let key = slot(on: desktop, in: slots)?.key
            let entry = NSMenuItem(title: key.map { "\(desktop)  \($0)" } ?? "\(desktop)",
                                   action: #selector(switchToDesktop(_:)), keyEquivalent: "")
            entry.target = self
            entry.tag = desktop
            entry.isEnabled = shortcuts[desktop] != nil
            menu.addItem(entry)
        }
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit SlotBar", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        return menu
    }

    // Sends the Desktop's shortcut, read fresh in case it changed in System Settings. Switching
    // brings no window forward, so a dialog on that Desktop stays on top.
    @objc func switchToDesktop(_ sender: NSMenuItem) {
        guard let shortcut = readDesktopShortcuts()[sender.tag] else { return }
        guard AXIsProcessTrusted() else {
            print("switch to Desktop \(sender.tag): no Accessibility grant")
            requestAccessibility()
            return
        }
        // The menu is still closing; a shortcut sent now can land on it instead of the Dock.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { sendKey(shortcut) }
    }
}

func readDesktopShortcuts() -> [Int: DesktopShortcut] {
    parseDesktopShortcuts(UserDefaults.standard.persistentDomain(forName: "com.apple.symbolichotkeys") ?? [:])
}

func sendKey(_ shortcut: DesktopShortcut) {
    let source = CGEventSource(stateID: .hidSystemState)
    for isDown in [true, false] {
        let event = CGEvent(keyboardEventSource: source, virtualKey: shortcut.keyCode, keyDown: isDown)
        event?.flags = CGEventFlags(rawValue: shortcut.modifierMask)
        event?.post(tap: .cghidEventTap)
    }
}

// Shows macOS's prompt to grant Accessibility when SlotBar doesn't have it yet.
func requestAccessibility() {
    let prompt = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
    _ = AXIsProcessTrustedWithOptions([prompt: true] as CFDictionary)
}

@main
struct SlotBarApp {
    static func main() {
        let args = CommandLine.arguments
        guard args.count == 2 else {
            print("usage: SlotBar <slots file>")
            exit(2)
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        if !AXIsProcessTrusted() { requestAccessibility() }
        let bar = SlotBar(slotsPath: args[1])
        withExtendedLifetime(bar) { app.run() }
    }
}
