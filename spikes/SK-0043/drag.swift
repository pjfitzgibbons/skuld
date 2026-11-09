// SK-0043 spike: drag.swift <x> <y> <desktop>: hold the mouse on (x,y), press ^<desktop>, release.
import Foundation
import CoreGraphics
let a = CommandLine.arguments
let p = CGPoint(x: Double(a[1])!, y: Double(a[2])!)
let key = CGKeyCode(17 + Int(a[3])!)
let src = CGEventSource(stateID: .hidSystemState)
func mouse(_ t: CGEventType, _ q: CGPoint) { CGEvent(mouseEventSource: src, mouseType: t, mouseCursorPosition: q, mouseButton: .left)!.post(tap: .cghidEventTap) }
func ctrlKey(_ down: Bool) { let e = CGEvent(keyboardEventSource: src, virtualKey: key, keyDown: down)!; e.flags = .maskControl; e.post(tap: .cghidEventTap) }
mouse(.mouseMoved, p); usleep(100_000)
mouse(.leftMouseDown, p); usleep(500_000)
for i in 1...20 { mouse(.leftMouseDragged, CGPoint(x: p.x + Double(i * 3), y: p.y + Double(i))); usleep(30_000) }
usleep(300_000)
ctrlKey(true); usleep(50_000); ctrlKey(false)
for i in 1...20 { mouse(.leftMouseDragged, CGPoint(x: p.x + 60 + Double(i), y: p.y + 20)); usleep(75_000) }
mouse(.leftMouseUp, CGPoint(x: p.x + 80, y: p.y + 20))
