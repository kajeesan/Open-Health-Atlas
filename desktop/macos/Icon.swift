// Open Health Atlas Personal Contour Mark.
// Original artwork. Copyright (c) 2026 Kajeesan Jeevendra. AGPL-3.0-only.
// Geometry and colours match the canonical desktop/macos/icon.svg (512-unit viewBox).
import AppKit

let destination = CommandLine.arguments[1]
let context = CGContext(data: nil, width: 1024, height: 1024,
    bitsPerComponent: 8, bytesPerRow: 1024 * 4,
    space: CGColorSpace(name: CGColorSpace.sRGB)!,
    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
context.clear(CGRect(x: 0, y: 0, width: 1024, height: 1024))
// Convert the SVG's top-left origin to the bitmap's bottom-left origin at 2x.
context.translateBy(x: 0, y: 1024)
context.scaleBy(x: 2, y: -2)
context.setAllowsAntialiasing(true)
context.setShouldAntialias(true)

func rgb(_ red: CGFloat, _ green: CGFloat, _ blue: CGFloat) -> CGColor {
    CGColor(srgbRed: red / 255, green: green / 255, blue: blue / 255, alpha: 1)
}
let teal = rgb(12, 64, 66)
let cream = rgb(242, 243, 218)
let coral = rgb(245, 139, 104)
context.setFillColor(teal)
context.addPath(CGPath(roundedRect: CGRect(x: 20, y: 20, width: 472, height: 472),
    cornerWidth: 108, cornerHeight: 108, transform: nil))
context.fillPath()

typealias Cubic = (CGFloat, CGFloat, CGFloat, CGFloat, CGFloat, CGFloat)
func contour(from start: CGPoint, curves: [Cubic], width: CGFloat, color: CGColor) {
    let path = CGMutablePath()
    path.move(to: start)
    for curve in curves {
        path.addCurve(to: CGPoint(x: curve.4, y: curve.5),
                      control1: CGPoint(x: curve.0, y: curve.1),
                      control2: CGPoint(x: curve.2, y: curve.3))
    }
    context.addPath(path)
    context.setStrokeColor(color)
    context.setLineWidth(width)
    context.setLineCap(.round)
    context.strokePath()
}
contour(from: CGPoint(x: 119, y: 343), curves: [
    (78, 245, 124, 130, 223, 96), (324, 61, 431, 129, 439, 234)
], width: 20, color: cream)
contour(from: CGPoint(x: 151, y: 371), curves: [
    (97, 280, 131, 165, 218, 129), (311, 90, 405, 150, 405, 244),
    (405, 318, 367, 380, 316, 417)
], width: 18, color: cream)
contour(from: CGPoint(x: 184, y: 389), curves: [
    (137, 317, 153, 211, 223, 167), (293, 123, 370, 178, 371, 253),
    (372, 318, 335, 365, 286, 392)
], width: 16, color: cream)
contour(from: CGPoint(x: 217, y: 393), curves: [
    (179, 337, 184, 253, 232, 207), (279, 162, 337, 211, 337, 264),
    (337, 311, 310, 344, 270, 363)
], width: 14, color: cream)
contour(from: CGPoint(x: 248, y: 360), curves: [
    (222, 324, 224, 272, 250, 248), (273, 226, 301, 248, 301, 276),
    (301, 297, 290, 316, 273, 327)
], width: 12, color: cream)
contour(from: CGPoint(x: 404, y: 244), curves: [
    (405, 286, 393, 321, 373, 351)
], width: 18, color: coral)
context.setFillColor(coral)
context.fillEllipse(in: CGRect(x: 384, y: 224, width: 40, height: 40))
let bitmap = NSBitmapImageRep(cgImage: context.makeImage()!)
try bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: destination))
