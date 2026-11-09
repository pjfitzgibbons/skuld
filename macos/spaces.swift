// skuld-spaces: the Desktops, the showing one, each window of one app with its Desktop, and the
// app's frontmost window on screen, as one line of JSON on stdout. Read-only; needs no grant and leaves SIP alone. Built by bin/build-spaces.
//
//   skuld-spaces [bundle id]      default com.microsoft.VSCode
//
// Desktops are numbered the way "Switch to Desktop N" (^N) counts them: across displays in the
// order CGSCopyManagedDisplaySpaces lists them, counting only desktop Spaces (type 0), never a
// full-screen one. A window whose Space isn't a numbered Desktop has "desktop": null. "front" is the
// app's frontmost window on screen, or null when none of its windows is on screen.
//
// The SkyLight calls are private, so each is resolved with dlsym at run time. A missing symbol
// prints {"unknown": "<symbol>"} and exits 3: the caller is told it can't know, never handed a
// guessed Desktop. SKULD_SPACES_HIDE (comma-separated symbol names) makes dlsym miss on purpose,
// to try that path.
import AppKit

typealias MainConnectionID = @convention(c) () -> Int32
typealias CopyManagedDisplaySpaces = @convention(c) (Int32) -> Unmanaged<CFArray>?
typealias CopySpacesForWindows = @convention(c) (Int32, Int32, CFArray) -> Unmanaged<CFArray>?

let desktopSpaceType = 0
let allSpacesMask: Int32 = 0x7

func emit(_ object: Any) {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

func unknown(_ symbol: String) -> Never {
    emit(["unknown": symbol])
    exit(3)
}

func hiddenSymbols() -> Set<String> {
    let names = ProcessInfo.processInfo.environment["SKULD_SPACES_HIDE"] ?? ""
    return Set(names.split(separator: ",").map { String($0) })
}

func resolve<T>(_ symbol: String, as type: T.Type) -> T {
    let defaultHandle = UnsafeMutableRawPointer(bitPattern: -2)  // RTLD_DEFAULT
    guard !hiddenSymbols().contains(symbol), let pointer = dlsym(defaultHandle, symbol) else {
        unknown(symbol)
    }
    return unsafeBitCast(pointer, to: type)
}

let connection = resolve("CGSMainConnectionID", as: MainConnectionID.self)()
let copyDisplaySpaces = resolve("CGSCopyManagedDisplaySpaces", as: CopyManagedDisplaySpaces.self)
let copySpacesForWindows = resolve("CGSCopySpacesForWindows", as: CopySpacesForWindows.self)

struct Desktop { let number: Int; let spaceID: Int; let display: String; let showing: Bool }

func readDesktops() -> [Desktop] {
    guard let displays = copyDisplaySpaces(connection)?.takeRetainedValue() as? [[String: Any]] else {
        unknown("CGSCopyManagedDisplaySpaces")
    }
    var desktops: [Desktop] = []
    for display in displays {
        let uuid = display["Display Identifier"] as? String ?? "?"
        let current = (display["Current Space"] as? [String: Any])?["ManagedSpaceID"] as? Int
        for space in (display["Spaces"] as? [[String: Any]]) ?? [] {
            guard (space["type"] as? Int) == desktopSpaceType, let id = space["ManagedSpaceID"] as? Int else { continue }
            desktops.append(Desktop(number: desktops.count + 1, spaceID: id, display: uuid, showing: id == current))
        }
    }
    return desktops
}

func appWindowIDs(bundleID: String) -> [Int] {
    let pids = Set(NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).map { $0.processIdentifier })
    let info = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    return info.compactMap { window in
        guard (window[kCGWindowLayer as String] as? Int) == 0,
              let pid = window[kCGWindowOwnerPID as String] as? Int32, pids.contains(pid) else { return nil }
        return window[kCGWindowNumber as String] as? Int
    }
}

// CGWindowList lists on-screen windows front to back, so the first of the app's is the frontmost.
func frontWindowID(bundleID: String) -> Int? {
    let pids = Set(NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).map { $0.processIdentifier })
    let info = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
    let front = info.first { window in
        guard (window[kCGWindowLayer as String] as? Int) == 0,
              let pid = window[kCGWindowOwnerPID as String] as? Int32 else { return false }
        return pids.contains(pid)
    }
    return front?[kCGWindowNumber as String] as? Int
}

func spaceIDs(ofWindow id: Int) -> [Int] {
    copySpacesForWindows(connection, allSpacesMask, [id] as CFArray)?.takeRetainedValue() as? [Int] ?? []
}

let bundleID = CommandLine.arguments.dropFirst().first ?? "com.microsoft.VSCode"
let desktops = readDesktops()
let desktopBySpace = Dictionary(uniqueKeysWithValues: desktops.map { ($0.spaceID, $0.number) })
let windows: [[String: Any]] = appWindowIDs(bundleID: bundleID).compactMap { id in
    let spaces = spaceIDs(ofWindow: id)
    if spaces.isEmpty { return nil }  // not on any Space: a hidden helper window, not a document window
    return ["id": id, "desktop": spaces.compactMap { desktopBySpace[$0] }.first ?? NSNull()]
}
emit([
    "desktops": desktops.map { ["number": $0.number, "display": $0.display, "showing": $0.showing] },
    "windows": windows,
    "front": frontWindowID(bundleID: bundleID).map { $0 as Any } ?? NSNull(),
])
