// slotbar-pulse <desktop> <orange|green|off>: asks SlotBar to pulse a slot, or to clear its tint.
// Exits 2 on bad arguments and 1 when SlotBar isn't running, since nobody would see the pulse (L5).
import AppKit

@main
struct PulseCommand {
    static func main() {
        let args = CommandLine.arguments
        guard args.count == 3, let pulse = validatePulse(slot: args[1], color: args[2]) else {
            fail("usage: slotbar-pulse <desktop 1-\(maxDesktop)> <orange|green|off>", status: 2)
        }
        guard slotBarIsRunning() else {
            fail("slotbar-pulse: SlotBar isn't running (bundle \(slotBarBundleID)); see GUIDE §2", status: 1)
        }
        DistributedNotificationCenter.default().postNotificationName(
            pulseNotification, object: nil, userInfo: pulseUserInfo(pulse), deliverImmediately: true)
    }

    static func slotBarIsRunning() -> Bool {
        !NSRunningApplication.runningApplications(withBundleIdentifier: slotBarBundleID).isEmpty
    }

    static func fail(_ message: String, status: Int32) -> Never {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        exit(status)
    }
}
