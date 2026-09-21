// Open Health Atlas installer artwork.
// Copyright (c) 2026 Kajeesan Jeevendra. AGPL-3.0-only.
import AppKit

// Finder supplies the two real draggable icons and their accessible labels.
// This background only supplies the instruction and the directional cue.
let destination = CommandLine.arguments[1]
let scale = Int(CommandLine.arguments[2])!
let width = 640, height = 400
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width * scale,
    pixelsHigh: height * scale, bitsPerSample: 8, samplesPerPixel: 4,
    hasAlpha: true, isPlanar: false, colorSpaceName: .calibratedRGB,
    bytesPerRow: 0, bitsPerPixel: 0)!
bitmap.size = NSSize(width: width, height: height)
let context = NSGraphicsContext(bitmapImageRep: bitmap)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = context
// NSGraphicsContext scales from bitmap.size to pixels, including Retina.
func color(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat) -> NSColor {
    NSColor(srgbRed: r / 255, green: g / 255, blue: b / 255, alpha: 1)
}
color(239, 236, 231).setFill()
NSRect(x: 0, y: 0, width: width, height: height).fill()
func centered(_ text: String, y: CGFloat, font: NSFont, ink: NSColor) {
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = .center
    (text as NSString).draw(in: NSRect(x: 28, y: y, width: 584, height: 42),
        withAttributes: [.font: font, .foregroundColor: ink, .paragraphStyle: paragraph])
}
centered("Make room for a clearer picture.", y: 326,
    font: NSFont(name: "Georgia", size: 27)!, ink: color(35, 34, 29))
centered("Drag the app into Applications to install.", y: 287,
    font: .systemFont(ofSize: 15), ink: color(98, 95, 85))
let arrow = NSBezierPath()
arrow.move(to: NSPoint(x: 285, y: 200))
arrow.line(to: NSPoint(x: 352, y: 200))
arrow.move(to: NSPoint(x: 337, y: 215))
arrow.line(to: NSPoint(x: 352, y: 200))
arrow.line(to: NSPoint(x: 337, y: 185))
arrow.lineWidth = 5
arrow.lineCapStyle = .round
arrow.lineJoinStyle = .round
color(12, 64, 66).setStroke()
arrow.stroke()
centered("Then open Open Health Atlas from Applications.", y: 37,
    font: .systemFont(ofSize: 13), ink: color(98, 95, 85))
NSGraphicsContext.restoreGraphicsState()
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: destination))
