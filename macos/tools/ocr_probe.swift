// ocr_probe — M3 scoping tool. Capture a desktop-app window (ScreenCaptureKit) and OCR it
// (Apple Vision, on-device). Proves whether Vision can read the agent app's sidebar project /
// session names and at what coordinates — the data the Exact-Resume desktop resolver needs,
// since Claude.app is AX-opaque (only AXWebArea, no AXTextArea). No LLM. Read-only (no clicks).
//
//   swiftc -O macos/tools/ocr_probe.swift -o build/ocr_probe \
//       -framework ScreenCaptureKit -framework Vision -framework AppKit
//   build/ocr_probe Claude        # needs Screen Recording permission (TCC will prompt once)
import Foundation
import ScreenCaptureKit
import Vision
import AppKit

let appName = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "Claude"

func findWindow(_ name: String) async -> SCWindow? {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        let wins = content.windows.filter { w in
            guard let app = w.owningApplication else { return false }
            return app.applicationName.localizedCaseInsensitiveContains(name) && w.frame.width > 200
        }
        return wins.sorted { ($0.frame.width * $0.frame.height) > ($1.frame.width * $1.frame.height) }.first
    } catch {
        FileHandle.standardError.write("SCShareableContent error (grant Screen Recording?): \(error)\n".data(using: .utf8)!)
        return nil
    }
}

func capture(_ win: SCWindow) async -> CGImage? {
    let filter = SCContentFilter(desktopIndependentWindow: win)
    let cfg = SCStreamConfiguration()
    cfg.width = Int(win.frame.width * 2)      // 2x for Retina sharpness (helps small sidebar text)
    cfg.height = Int(win.frame.height * 2)
    cfg.showsCursor = false
    do {
        return try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
    } catch {
        FileHandle.standardError.write("capture error: \(error)\n".data(using: .utf8)!)
        return nil
    }
}

func ocr(_ img: CGImage) {
    let W = Double(img.width), H = Double(img.height)
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["ko-KR", "en-US"]
    req.usesLanguageCorrection = false
    let handler = VNImageRequestHandler(cgImage: img, options: [:])
    try? handler.perform([req])
    guard let obs = req.results else { print("no text"); return }
    print("recognized \(obs.count) text runs (region = normalized x<0.38 sidebar / y<0.10 topbar / y>0.88 composer):")
    for o in obs {
        guard let top = o.topCandidates(1).first else { continue }
        let bb = o.boundingBox                      // normalized, origin bottom-left
        let cx = (bb.origin.x + bb.width / 2) * W    // pixel center (top-left origin)
        let cy = (1 - (bb.origin.y + bb.height / 2)) * H
        let region = bb.origin.x < 0.38 ? "sidebar"
            : (bb.origin.y > 0.90 ? "topbar" : (bb.origin.y < 0.12 ? "composer" : "content"))
        let conf = String(format: "%.2f", top.confidence)
        let cxs = String(format: "%5.0f", cx), cys = String(format: "%5.0f", cy)
        print("  [\(region)] conf=\(conf) cx=\(cxs) cy=\(cys)  \(top.string)")
    }
}

// Capture needs a WindowServer connection — run inside an NSApplication context (a bare CLI
// hits "CGS_REQUIRE_INIT"). This mirrors how the real resolver must live INSIDE NonyaPet.app.
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
Task {
    if let win = await findWindow(appName) {
        print("window: \(Int(win.frame.width))x\(Int(win.frame.height)) origin=(\(Int(win.frame.origin.x)),\(Int(win.frame.origin.y))) title=\(win.title ?? "")")
        if let img = await capture(win) {
            print("captured \(img.width)x\(img.height) px")
            ocr(img)
        } else {
            print("capture failed")
        }
    } else {
        print("no window for \(appName)")
    }
    exit(0)
}
app.run()
