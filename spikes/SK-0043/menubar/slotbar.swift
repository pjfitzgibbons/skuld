// SK-0043 spike, item 5: a menu-bar extra that shows issue slots on native Desktops.
// Usage: slotbar <slots file> <workspace dir>
//   slots file: lines "N=KEY" (Desktop number = issue key); workspace dir holds KEY.code-workspace.
// Pulse from the shell: a distributed notification "skuld.pulse" with userInfo {slot, color}.
import AppKit

typealias Conn = Int32
@_silgen_name("CGSMainConnectionID") func CGSMainConnectionID() -> Conn
@_silgen_name("CGSCopyManagedDisplaySpaces") func CGSCopyManagedDisplaySpaces(_ cid: Conn) -> CFArray

struct Slot { let desktop: Int; let key: String }

// Desktop numbers on the main display, and which one is showing.
func desktops() -> (count: Int, active: Int) {
    let displays = CGSCopyManagedDisplaySpaces(CGSMainConnectionID()) as? [[String: Any]] ?? []
    guard let main = displays.first,
          let spaces = main["Spaces"] as? [[String: Any]] else { return (0, 0) }
    let currentID = (main["Current Space"] as? [String: Any])?["ManagedSpaceID"] as? Int
    let ids = spaces.map { $0["ManagedSpaceID"] as? Int }
    let activeIndex = ids.firstIndex { $0 == currentID } ?? -1
    return (spaces.count, activeIndex + 1)
}

func readSlots(_ path: String) -> [Slot] {
    let text = (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
    return text.split(separator: "\n").compactMap { line in
        let parts = line.split(separator: "=", maxSplits: 1).map(String.init)
        guard parts.count == 2, let n = Int(parts[0]) else { return nil }
        return Slot(desktop: n, key: parts[1])
    }
}

final class SlotBar: NSObject {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let slotsPath: String
    let workspaceDir: String
    var pulses: [Int: (color: NSColor, started: Date)] = [:]
    let pulseSeconds = 3.0

    init(slotsPath: String, workspaceDir: String) {
        self.slotsPath = slotsPath
        self.workspaceDir = workspaceDir
        super.init()
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(redraw), name: NSWorkspace.activeSpaceDidChangeNotification, object: nil)
        DistributedNotificationCenter.default().addObserver(
            self, selector: #selector(pulse(_:)), name: Notification.Name("skuld.pulse"), object: nil)
        Timer.scheduledTimer(withTimeInterval: 1.0 / 20, repeats: true) { [weak self] _ in self?.tick() }
        Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.redraw() }
        redraw()
    }

    @objc func pulse(_ note: Notification) {
        guard let info = note.userInfo,
              let slot = Int("\(info["slot"] ?? "")") else { return }
        let color: NSColor = "\(info["color"] ?? "")" == "green" ? .systemGreen : .systemRed
        pulses[slot] = (color, Date())
        redraw()
    }

    func tick() {
        guard !pulses.isEmpty else { return }
        redraw()
    }

    // A pulse fades in and out (sin) for pulseSeconds, then the slot keeps a steady tint.
    func background(for desktop: Int) -> NSColor? {
        guard let pulse = pulses[desktop] else { return nil }
        let elapsed = Date().timeIntervalSince(pulse.started)
        if elapsed >= pulseSeconds { return pulse.color.withAlphaComponent(0.6) }
        let alpha = abs(sin(elapsed * .pi * 2 / 1.0))
        return pulse.color.withAlphaComponent(alpha)
    }

    func segment(desktop: Int, slot: Slot?, active: Bool) -> NSAttributedString {
        let text = slot == nil ? " \(desktop) " : " \(desktop)• "
        var attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.menuBarFont(ofSize: 0),
            .foregroundColor: active ? NSColor.labelColor : NSColor.secondaryLabelColor,
        ]
        if active { attrs[.underlineStyle] = NSUnderlineStyle.single.rawValue }
        if let bg = background(for: desktop) { attrs[.backgroundColor] = bg }
        return NSAttributedString(string: text, attributes: attrs)
    }

    @objc func redraw() {
        let (count, active) = desktops()
        let slots = readSlots(slotsPath)
        let title = NSMutableAttributedString()
        for desktop in 1...max(count, 1) {
            let slot = slots.first { $0.desktop == desktop }
            title.append(segment(desktop: desktop, slot: slot, active: desktop == active))
        }
        item.button?.attributedTitle = title
        item.menu = menu(for: slots)
        reportPlacement()
    }

    // Where macOS put the item: a hidden item has no window on screen or sits off the visible bar.
    func reportPlacement() {
        let frame = item.button?.window?.frame ?? .zero
        let line = "item frame=\(frame) visible=\(item.button?.window?.isVisible ?? false) occlusion=\(item.button?.window?.occlusionState.contains(.visible) ?? false)"
        if line != lastPlacement { print(line); fflush(stdout); lastPlacement = line }
    }
    var lastPlacement = ""

    func menu(for slots: [Slot]) -> NSMenu {
        let menu = NSMenu()
        for slot in slots {
            let entry = NSMenuItem(title: "\(slot.desktop)  \(slot.key)", action: #selector(focus(_:)), keyEquivalent: "")
            entry.target = self
            entry.representedObject = slot.key
            menu.addItem(entry)
        }
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        return menu
    }

    // `code <workspace>` focuses the window that has it open and switches to its Desktop.
    @objc func focus(_ sender: NSMenuItem) {
        guard let key = sender.representedObject as? String else { return }
        let code = Process()
        code.executableURL = URL(fileURLWithPath: "/usr/local/bin/code")
        code.arguments = ["\(workspaceDir)/\(key).code-workspace"]
        try? code.run()
    }
}

let args = CommandLine.arguments
guard args.count == 3 else { print("usage: slotbar <slots file> <workspace dir>"); exit(2) }
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let bar = SlotBar(slotsPath: args[1], workspaceDir: args[2])
app.run()
