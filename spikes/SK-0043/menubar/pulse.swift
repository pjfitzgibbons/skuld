// SK-0043 spike: pulse <slot> <red|green>, posts the "skuld.pulse" distributed notification.
import Foundation
let a = CommandLine.arguments
guard a.count == 3 else { print("usage: pulse <slot> <red|green>"); exit(2) }
DistributedNotificationCenter.default().postNotificationName(
    Notification.Name("skuld.pulse"), object: nil, userInfo: ["slot": a[1], "color": a[2]], deliverImmediately: true)
