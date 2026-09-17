// Original Open Health Atlas icon. Copyright (c) 2026 Kajeesan Jeevendra. MIT.
import AppKit
let destination = CommandLine.arguments[1]
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
NSColor(calibratedRed: 0.055, green: 0.24, blue: 0.24, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 40, y: 40, width: 944, height: 944), xRadius: 216, yRadius: 216).fill()
let cream = NSColor(calibratedRed: 0.92, green: 0.94, blue: 0.84, alpha: 1)
cream.setStroke()
let globe = NSBezierPath(ovalIn: NSRect(x: 214, y: 214, width: 596, height: 596)); globe.lineWidth = 34; globe.stroke()
let meridian = NSBezierPath(ovalIn: NSRect(x: 368, y: 214, width: 288, height: 596)); meridian.lineWidth = 20; meridian.stroke()
for y in [390.0, 634.0] { let line = NSBezierPath(); line.move(to: NSPoint(x: 244, y: y)); line.line(to: NSPoint(x: 780, y: y)); line.lineWidth = 20; line.stroke() }
let equator = NSBezierPath(); equator.move(to: NSPoint(x: 214, y: 512)); equator.line(to: NSPoint(x: 810, y: 512)); equator.lineWidth = 20; equator.stroke()
NSColor(calibratedRed: 0.96, green: 0.55, blue: 0.39, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 675, y: 660, width: 156, height: 156)).fill()
image.unlockFocus()
let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: destination))
