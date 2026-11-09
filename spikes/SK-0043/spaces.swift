// SK-0043 spike: read-only SkyLight queries. Usage: spaces [owner-name-filter]
import Foundation
import CoreGraphics

typealias Conn = Int32
@_silgen_name("CGSMainConnectionID") func CGSMainConnectionID() -> Conn
@_silgen_name("CGSCopyManagedDisplaySpaces") func CGSCopyManagedDisplaySpaces(_ cid: Conn) -> CFArray
@_silgen_name("CGSGetActiveSpace") func CGSGetActiveSpace(_ cid: Conn) -> UInt64
@_silgen_name("CGSCopySpacesForWindows") func CGSCopySpacesForWindows(_ cid: Conn, _ mask: Int32, _ wids: CFArray) -> CFArray

let cid = CGSMainConnectionID()
print("active space: \(CGSGetActiveSpace(cid))")

func spaceType(_ t: Int) -> String { [0: "desktop", 4: "fullscreen"][t] ?? "type\(t)" }

let displays = CGSCopyManagedDisplaySpaces(cid) as! [[String: Any]]
for (d, display) in displays.enumerated() {
    let uuid = display["Display Identifier"] as? String ?? "?"
    let current = (display["Current Space"] as? [String: Any])?["ManagedSpaceID"] as? Int ?? -1
    print("display \(d + 1) \(uuid) current=\(current)")
    for (n, space) in ((display["Spaces"] as? [[String: Any]]) ?? []).enumerated() {
        let id = space["ManagedSpaceID"] as? Int ?? -1
        let type = space["type"] as? Int ?? -1
        print("  Desktop \(n + 1): id=\(id) \(spaceType(type))\(id == current ? " *" : "")")
    }
}

let filter = CommandLine.arguments.dropFirst().first
let info = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as! [[String: Any]]
for w in info where (w[kCGWindowLayer as String] as? Int) == 0 {
    let owner = w[kCGWindowOwnerName as String] as? String ?? ""
    if let f = filter, !owner.contains(f) { continue }
    let wid = w[kCGWindowNumber as String] as! Int
    let name = w[kCGWindowName as String] as? String ?? "(no title)"
    let spaces = CGSCopySpacesForWindows(cid, 0x7, [wid] as CFArray) as! [Int]
    print("window \(wid) \(owner) \"\(name)\" spaces=\(spaces)")
}
