// NonyaPet — the menu-bar FACE for the nonya Correctness Supervisor. A live
// status-bar "eyes" widget (5 selectable styles) mirrors the agent's state from
// ~/.local/state/nonya/state.json: watching / scolding / stuck / waiting / looping /
// working / done / stopped. The menu spawns the Python core (the muscle) to watch a
// target (Claude/Codex, app or CLI) and shows the wake-up briefing. No character,
// no overlay — just the eyes + the supervisor.
import AppKit
import ApplicationServices
import Foundation
import ScreenCaptureKit
import UserNotifications
import Vision

// MARK: - state feed

struct NonyaState: Decodable {
    var status: String?
    var character: String?
    var nudges: Int?
    var scold: String?
    var target: String?
}
func stateURL() -> URL {
    FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/state/nonya/state.json")
}
func readState() -> NonyaState? {
    guard let d = try? Data(contentsOf: stateURL()) else { return nil }
    return try? JSONDecoder().decode(NonyaState.self, from: d)
}
func readStateRaw() -> [String: Any] {
    guard let d = try? Data(contentsOf: stateURL()),
          let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return [:] }
    return o
}
// urgency rank for the eyes — when several sessions are watched at once (e.g. "watch all"),
// the face must show the MOST urgent one, not whichever core wrote state.json last.
private let _moodRank: [String: Int] = [
    "waiting": 60, "needs-you": 60, "looping": 50, "scolding": 50, "stuck": 40,
    "rate-limited": 35, "verify-failed": 30, "working": 20, "watching": 20,
    "done": 10, "stopped": 5, "idle": 1,
]
// Most urgent live session status across <state>/sessions/*.json (skips files >30min stale),
// falling back to the single legacy state.json for one-session runs.
func urgentStatus() -> String? {
    let fm = FileManager.default
    let dir = stateURL().deletingLastPathComponent().appendingPathComponent("sessions")
    var best: (Int, String)?
    if let files = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey]) {
        for f in files where f.pathExtension == "json" {
            if let m = (try? f.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate,
               Date().timeIntervalSince(m) > 1800 {                 // dead/crashed core: ignore AND clean up
                try? fm.removeItem(at: f)                           // else single-session files pile up over days
                continue
            }
            guard let d = try? Data(contentsOf: f),
                  let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
                  let st = obj["status"] as? String else { continue }
            if st == "idle" { continue }
            let r = _moodRank[st] ?? 0
            if best == nil || r > best!.0 { best = (r, st) }
        }
    }
    return best?.1 ?? readState()?.status
}
func nonyaBinary() -> (URL, [String]) {
    let fm = FileManager.default
    if let res = Bundle.main.resourceURL?.appendingPathComponent("core/nonya").path,
       fm.isExecutableFile(atPath: res) { return (URL(fileURLWithPath: res), []) }
    let dev = fm.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/nonya").path
    if fm.isExecutableFile(atPath: dev) { return (URL(fileURLWithPath: dev), []) }
    return (URL(fileURLWithPath: "/usr/bin/env"), ["nonya"])
}
// ===== AX terminal-split injection =====
// Native terminals (Ghostty/iTerm/Terminal) expose each split as an AXTextArea whose
// content we can READ — so we identify the right split by content, FOCUS it (AXFocused is
// settable), then paste. This recovers a stalled session in a BACKGROUND split with NO tmux,
// NO vision model — deterministic, and refuses to act when the match is ambiguous (misfire 0).
private func _axAttr(_ e: AXUIElement, _ a: String) -> AnyObject? {
    var v: CFTypeRef?; return AXUIElementCopyAttributeValue(e, a as CFString, &v) == .success ? v : nil
}
// Whitespace-insensitive, lowercased — so a needle that wraps across terminal lines (the AX value
// inserts a newline at the wrap column) still matches, and verification isn't fooled by wrapping.
private func _norm(_ s: String) -> String { s.lowercased().filter { !$0.isWhitespace } }
private func _axTextAreas(_ e: AXUIElement, _ depth: Int = 0, _ acc: inout [AXUIElement]) {
    if (_axAttr(e, "AXRole") as? String) == "AXTextArea" { acc.append(e) }
    if depth < 10, let kids = _axAttr(e, "AXChildren") as? [AXUIElement] {
        for c in kids { _axTextAreas(c, depth + 1, &acc) }
    }
}
// Match a running app by name ROBUSTLY across locales: the caller passes the System-Events
// process name (e.g. "Terminal", "ghostty"), but NSRunningApplication.localizedName is
// LOCALIZED ("터미널" on a Korean Mac) — matching only that fails on non-English systems.
// So match the executable leaf (Terminal.app/Contents/MacOS/Terminal -> "Terminal") and the
// bundle id too, all case-insensitively. (This was a real bug: Terminal.app recovery returned
// rc 2 "no app" on a Korean Mac because localizedName was "터미널".)
private func _runningApp(_ appName: String) -> NSRunningApplication? {
    let q = appName.lowercased()
    return NSWorkspace.shared.runningApplications.first { a in
        if let n = a.localizedName?.lowercased(), n == q { return true }
        if let e = a.executableURL?.lastPathComponent.lowercased(), e == q { return true }
        if let b = a.bundleIdentifier?.lowercased(), b == q || b.hasSuffix("." + q) { return true }
        return false
    }
}
// Returns: 0 ok, 2 no app, 3 no/!1 match (ambiguous -> caller alerts), 4 focus/paste failed.
func axInjectSplit(appName: String, match: String, text: String, sendKey: String) -> Int32 {
    guard let app = _runningApp(appName) else { return 2 }
    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    // AXManualAccessibility makes the terminal expose its split AX tree WITHOUT being frontmost,
    // so we never have to activate it (no focus-steal, no fullscreen-Space jump). Retry for lazy pop.
    AXUIElementSetAttributeValue(axApp, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    var areas: [AXUIElement] = []
    for _ in 0..<8 {
        areas = []
        for w in (_axAttr(axApp, "AXWindows") as? [AXUIElement]) ?? [] { _axTextAreas(w, 0, &areas) }
        if !areas.isEmpty { break }
        usleep(250_000)
    }
    let needle = _norm(match)
    let hits = areas.filter { _norm((_axAttr($0, "AXValue") as? String) ?? "").contains(needle) }
    guard hits.count == 1, !needle.isEmpty else { return 3 }   // 0 or >1 -> don't guess
    return _pasteVerify(hits[0], app.processIdentifier, text, sendKey)
}

// Focus a split, clear its input line, paste `text`, VERIFY it landed, then Return. A backgrounded
// native-Cocoa terminal (Terminal.app) silently DROPS posted key events, so the paste can no-op
// while the helper still "succeeds" — a false "recovered". We confirm the nudge text actually
// appears in the split before pressing Return; if not, restore the clipboard and return 4 so the
// caller ALERTS the human instead of lying. Returns 0 only on CONFIRMED delivery. Shared by the
// content-matched inject and the focused-split delivery test.
func _pasteVerify(_ split: AXUIElement, _ pid: pid_t, _ text: String, _ sendKey: String) -> Int32 {
    AXUIElementSetAttributeValue(split, "AXFocused" as CFString, kCFBooleanTrue)   // focus split WITHIN the (still-background) app
    usleep(180_000)
    let before = (_axAttr(split, "AXValue") as? String) ?? ""
    guard let src = CGEventSource(stateID: .combinedSessionState) else { return 4 }
    func tap(_ key: CGKeyCode, cmd: Bool = false, ctrl: Bool = false) {
        let d = CGEvent(keyboardEventSource: src, virtualKey: key, keyDown: true)
        let u = CGEvent(keyboardEventSource: src, virtualKey: key, keyDown: false)
        var f = CGEventFlags(); if cmd { f.insert(.maskCommand) }; if ctrl { f.insert(.maskControl) }
        d?.flags = f; u?.flags = f
        d?.postToPid(pid); u?.postToPid(pid)        // deliver to the process ONLY — no activation/Space switch
    }
    // Type the nudge as real UNICODE keystrokes (NOT clipboard Cmd+V): a paste keybinding needs the
    // window to be key, but raw posted key events reach a BACKGROUND split — so this delivers without
    // activation/focus-steal where Cmd+V silently no-ops. (Verified live on Ghostty.)
    func typeStr(_ s: String) {
        for ch in Array(s.utf16) {
            var u = ch
            let d = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true)
            d?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &u); d?.postToPid(pid)
            let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false)
            up?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &u); up?.postToPid(pid)
            usleep(7000)
        }
    }
    tap(0, ctrl: true); usleep(50_000)              // Ctrl+A — line start
    tap(11, ctrl: true); usleep(50_000)             // Ctrl+K — wipe any stray input so the prompt is clean
    typeStr(text); usleep(200_000)                  // type the nudge
    // VERIFY it landed before pressing Return — a backgrounded native-Cocoa terminal (Terminal.app)
    // drops posted key events, so confirm the text now appears (whitespace-insensitive, since long
    // input wraps). If not, report failure (4) so the caller ALERTS instead of a false "recovered".
    let after = (_axAttr(split, "AXValue") as? String) ?? ""
    if after == before || !_norm(after).contains(_norm(text)) { return 4 }
    if sendKey == "cmd+return" { tap(36, cmd: true) } else { tap(36) }   // Return — submit
    usleep(200_000)
    return 0
}

// TEST/diagnostic: type `text` into the matched split via UNICODE key events (no clipboard/Cmd+V)
// to a BACKGROUND window. Determines whether postToPid delivers plain typed chars when Cmd+V does
// not (Cmd+V/paste keybinding may require the window to be key; raw chars may not).
func axTypeMatch(appName: String, match: String, text: String) -> Int32 {
    guard let app = _runningApp(appName) else { return 2 }
    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetAttributeValue(axApp, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    var areas: [AXUIElement] = []
    for _ in 0..<8 {
        areas = []
        for w in (_axAttr(axApp, "AXWindows") as? [AXUIElement]) ?? [] { _axTextAreas(w, 0, &areas) }
        if !areas.isEmpty { break }
        usleep(250_000)
    }
    let needle = match.lowercased()
    let hits = areas.filter { ((_axAttr($0, "AXValue") as? String) ?? "").lowercased().contains(needle) }
    guard hits.count == 1 else { return 3 }
    let split = hits[0]
    AXUIElementSetAttributeValue(split, "AXFocused" as CFString, kCFBooleanTrue)
    usleep(150_000)
    guard let src = CGEventSource(stateID: .combinedSessionState) else { return 4 }
    let pid = app.processIdentifier
    for ch in Array(text.utf16) {
        var u = ch
        let d = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true)
        d?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &u); d?.postToPid(pid)
        let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false)
        up?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &u); up?.postToPid(pid)
        usleep(9000)
    }
    let r1 = CGEvent(keyboardEventSource: src, virtualKey: 36, keyDown: true); r1?.postToPid(pid)
    let r2 = CGEvent(keyboardEventSource: src, virtualKey: 36, keyDown: false); r2?.postToPid(pid)
    return 0
}

// TEST/diagnostic: inject into the FOCUSED split (or first textarea) WITHOUT a content match —
// isolates the postToPid DELIVERY mechanism (axInjectSplit exercises the content-match half).
func axInjectFocused(appName: String, text: String, sendKey: String) -> Int32 {
    guard let app = _runningApp(appName) else { return 2 }
    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetAttributeValue(axApp, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    var areas: [AXUIElement] = []
    for _ in 0..<8 {
        areas = []
        for w in (_axAttr(axApp, "AXWindows") as? [AXUIElement]) ?? [] { _axTextAreas(w, 0, &areas) }
        if !areas.isEmpty { break }
        usleep(250_000)
    }
    guard let split = areas.first(where: { (_axAttr($0, "AXFocused") as? Bool) == true }) ?? areas.first
        else { return 3 }
    return _pasteVerify(split, app.processIdentifier, text, sendKey)
}

// Terminal emulators whose splits expose AXTextArea (so a CLI session running inside can be focused
// by content). Claude/Codex DESKTOP apps are Electron (no AXTextArea), so a content match here is
// unambiguously a real terminal — that's how "전환" tells a CLI session apart from a desktop one.
let _TERMINALS = ["ghostty", "iTerm2", "iTerm", "Terminal", "WezTerm", "Warp",
                  "Alacritty", "kitty", "Hyper", "Tabby", "rio"]

// "전환" for a CLI session: find the terminal split whose on-screen content contains `needle` (the
// session's distinctive recent line), FOCUS that split (AXFocused) and raise its terminal. Focus
// only — no typing — so the AXFocused-vs-active-split injection hazard (which disabled auto-inject
// into raw splits) does not apply. Returns OK-terminal / NOT-FOUND / AMBIGUOUS / NO-NEEDLE.
func _focusTerminalSplit(_ needle: String) -> String {
    let n = _norm(needle)
    if n.count < 6 { return "NO-NEEDLE" }
    var hits: [(AXUIElement, NSRunningApplication)] = []
    for name in _TERMINALS {
        guard let app = _runningApp(name) else { continue }
        let axApp = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetAttributeValue(axApp, "AXManualAccessibility" as CFString, kCFBooleanTrue)
        var areas: [AXUIElement] = []
        for w in (_axAttr(axApp, "AXWindows") as? [AXUIElement]) ?? [] { _axTextAreas(w, 0, &areas) }
        for a in areas where _norm((_axAttr(a, "AXValue") as? String) ?? "").contains(n) {
            hits.append((a, app))
        }
    }
    if hits.isEmpty { return "NOT-FOUND" }
    if hits.count > 1 { return "AMBIGUOUS" }            // don't guess which split
    let (split, app) = hits[0]
    AXUIElementSetAttributeValue(split, "AXFocused" as CFString, kCFBooleanTrue)
    usleep(120_000)
    app.activate(options: [.activateAllWindows])         // bring the terminal to front
    return "OK-terminal"
}

// ===== M3: Vision-OCR desktop targeting (Exact Resume & Recovery) =====
// Claude/Codex are Electron — their sidebar/composer are NOT in the AX tree (only AXWebArea),
// so we CANNOT find a project/session row via AX. Instead we capture the window (ScreenCaptureKit)
// and read it with on-device Apple Vision OCR (no LLM, no cloud), score sidebar rows against the
// session's project hint (FR-104/105), and click the proven row. Refuses to act (AMBIGUOUS_TARGET)
// rather than guess — the PRD rule that fixes "keys landed in the wrong project". Capture REQUIRES
// a GUI/WindowServer context, so this runs in-process inside NonyaPet.app (a bare CLI crashes
// CGS_REQUIRE_INIT). Needs Screen Recording (TCC), separate from Accessibility.

struct OcrRun { let text: String; let cx: Double; let cy: Double; let nx: Double; let ny: Double; let conf: Double }

// Observability: every resolve/focus step appends here so failures are diagnosable (not silent).
//   tail -f ~/.local/state/nonya/resolve.log
func _rlog(_ s: String) {
    let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/state/nonya")
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let f = dir.appendingPathComponent("resolve.log")
    let df = DateFormatter(); df.dateFormat = "HH:mm:ss"
    let line = "\(df.string(from: Date())) \(s)\n"
    guard let data = line.data(using: .utf8) else { return }
    if let h = try? FileHandle(forWritingTo: f) {
        defer { try? h.close() }
        h.seekToEndOfFile(); h.write(data)
    } else {
        try? data.write(to: f)
    }
}

// FR-104 normalization: lowercase, keep only alphanumerics (incl. unicode letters/Hangul/digits),
// drop spaces/punct/badges — so "• 노냐", "Code", "mapo-hyodobapsang" compare cleanly.
func _normName(_ s: String) -> String {
    String(String.UnicodeScalarView(s.lowercased().unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) }))
}

// ===== Name matching: alias map + Hangul romanization + fuzzy (en-slug ↔ localized sidebar name) =====
// The editor sidebar shows LOCALIZED project names (e.g. "나비오", "코드브레인") while a watched
// session's hint is an English slug (cwd basename / label, e.g. "navio", "code-brain"). Plain
// normalize+substring can never bridge those, so "전환" always failed (NOT-FOUND). We bridge with
// (1) a user-editable alias map and (2) Hangul→roman fuzzy matching.

func _nonyaStateDir() -> URL {
    let d = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/state/nonya")
    try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
    return d
}

// { "<english slug>": ["<sidebar name>", ...] }. Created with a commented template on first use so
// the format is discoverable; romanization already covers phonetic names, so only the misses need
// an entry. Keys/values are compared after _normName.
func _aliasMap() -> [String: [String]] {
    let url = _nonyaStateDir().appendingPathComponent("aliases.json")
    if let data = try? Data(contentsOf: url),
       let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] {
        var out: [String: [String]] = [:]
        for (k, v) in obj where !k.hasPrefix("_") {
            if let arr = v as? [String] { out[_normName(k)] = arr }
            else if let s = v as? String { out[_normName(k)] = [s] }
        }
        return out
    }
    let seed = """
    {
      "_README": "Map an English session slug (cwd basename or label) to the name your editor sidebar shows. Romanization already matches phonetic names (navio→나비오); add only the ones it misses.",
      "code-brain": ["코드브레인"],
      "omnigen-vault": ["옴니젠"],
      "vault": ["옴니젠"]
    }
    """
    try? seed.data(using: .utf8)?.write(to: url)
    return ["codebrain": ["코드브레인"], "omnigenvault": ["옴니젠"], "vault": ["옴니젠"]]
}

// Revised-Romanization-ish Hangul transliteration; ASCII letters/digits pass through, the rest drop.
func _romanize(_ s: String) -> String {
    let CHO = ["g","kk","n","d","tt","r","m","b","pp","s","ss","","j","jj","ch","k","t","p","h"]
    let JUNG = ["a","ae","ya","yae","eo","e","yeo","ye","o","wa","wae","oe","yo","u","wo","we","wi","yu","eu","ui","i"]
    let JONG = ["","g","kk","gs","n","nj","nh","d","l","lg","lm","lb","ls","lt","lp","lh","m","b","bs","s","ss","ng","j","ch","k","t","p","h"]
    var out = ""
    for u in s.lowercased().unicodeScalars {
        let v = u.value
        if v >= 0xAC00 && v <= 0xD7A3 {
            let idx = Int(v - 0xAC00)
            out += CHO[idx / 588]; out += JUNG[(idx % 588) / 28]; out += JONG[idx % 28]
        } else if (v >= 97 && v <= 122) || (v >= 48 && v <= 57) {
            out.unicodeScalars.append(u)
        }
    }
    return out
}

func _lev(_ a: [Character], _ b: [Character]) -> Int {
    if a.isEmpty { return b.count }; if b.isEmpty { return a.count }
    var prev = Array(0...b.count)
    var cur = [Int](repeating: 0, count: b.count + 1)
    for i in 1...a.count {
        cur[0] = i
        for j in 1...b.count {
            let cost = a[i - 1] == b[j - 1] ? 0 : 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        }
        swap(&prev, &cur)
    }
    return prev[b.count]
}
func _fuzzySim(_ a: String, _ b: String) -> Double {
    if a.isEmpty || b.isEmpty { return 0 }
    let m = max(a.count, b.count)
    return 1.0 - Double(_lev(Array(a), Array(b))) / Double(m)
}

// Needles to look for in the sidebar (the hint + its aliases, normalized), plus the normalized
// english hint used for romanized-row fuzzy compare.
func _matchNeedles(_ hint: String) -> (needles: [String], eng: String) {
    let eng = _normName(hint)
    var set = Set<String>()
    if !eng.isEmpty { set.insert(eng) }
    let map = _aliasMap()
    for key in [eng, _normName((hint as NSString).lastPathComponent)] where !key.isEmpty {
        for a in map[key] ?? [] { let n = _normName(a); if !n.isEmpty { set.insert(n) } }
    }
    return (Array(set), eng)
}

// Does an OCR'd text match the target? alias/substring on the localized name, else romanized fuzzy.
func _nameMatches(_ text: String, _ needles: [String], _ eng: String) -> Bool {
    let n = _normName(text); if n.isEmpty { return false }
    for needle in needles where !needle.isEmpty {
        if n == needle || n.contains(needle) || needle.contains(n) { return true }
    }
    if !eng.isEmpty { let rr = _romanize(text); if rr.count >= 2 && _fuzzySim(rr, eng) >= 0.72 { return true } }
    return false
}

func _scoreRow(_ r: OcrRun, _ needles: [String], _ eng: String) -> Double {
    let n = _normName(r.text); if n.isEmpty { return 0 }
    var s = 0.0
    for needle in needles where !needle.isEmpty {
        if n == needle { s = max(s, 0.60) }
        else if n.contains(needle) || needle.contains(n) { s = max(s, 0.35) }
    }
    // fuzzy: romanize the (often Korean) row, compare to the english hint — bridges 나비오↔navio,
    // 노냐↔nonya, 마포효도밥상↔mapohyodobapsang, 옴니젠↔omnigen with no alias entry.
    if !eng.isEmpty {
        let rr = _romanize(r.text)
        if rr.count >= 2 {
            let sim = _fuzzySim(rr, eng)
            if sim >= 0.66 { s = max(s, 0.30 + 0.28 * sim) }   // ~0.49–0.58
        }
    }
    s += 0.1 * r.conf
    return s
}

func _scWindow(_ name: String) async -> SCWindow? {
    guard #available(macOS 14.0, *),
          let content = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
    else { return nil }
    let q = name.lowercased()
    let wins = content.windows.filter {
        guard let app = $0.owningApplication else { return false }
        let n = app.applicationName.lowercased()
        return (n == q || n.contains(q)) && $0.frame.width > 200 && $0.frame.height > 200
    }
    return wins.sorted { ($0.frame.width * $0.frame.height) > ($1.frame.width * $1.frame.height) }.first
}

// Capture the app's main window and OCR it. Returns the window frame (global points), the capture
// scale (px/point), and recognized text runs with pixel centers + normalized positions.
func _captureOCR(_ appName: String) async -> (frame: CGRect, scale: Double, runs: [OcrRun])? {
    guard #available(macOS 14.0, *), let win = await _scWindow(appName) else { return nil }
    let scale = 2.0
    let filter = SCContentFilter(desktopIndependentWindow: win)
    let cfg = SCStreamConfiguration()
    cfg.width = Int(win.frame.width * scale); cfg.height = Int(win.frame.height * scale)
    cfg.showsCursor = false
    guard let img = try? await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
    else { return nil }
    let W = Double(img.width), H = Double(img.height)
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["ko-KR", "en-US"]
    req.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
    var runs: [OcrRun] = []
    for o in (req.results ?? []) {
        guard let top = o.topCandidates(1).first else { continue }
        let bb = o.boundingBox                              // normalized, origin bottom-left
        let nx = bb.origin.x + bb.width / 2
        let ny = 1 - (bb.origin.y + bb.height / 2)          // to top-left origin
        runs.append(OcrRun(text: top.string, cx: nx * W, cy: ny * H, nx: nx, ny: ny, conf: Double(top.confidence)))
    }
    return (win.frame, scale, runs)
}

func _mouseClick(_ pt: CGPoint) {
    let src = CGEventSource(stateID: .combinedSessionState)
    CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: pt, mouseButton: .left)?.post(tap: .cghidEventTap)
    usleep(40_000)
    CGEvent(mouseEventSource: src, mouseType: .leftMouseUp, mouseCursorPosition: pt, mouseButton: .left)?.post(tap: .cghidEventTap)
}

// Raise the app until it is actually frontmost (reuse the proven retry loop). Returns false if it
// never comes front (then we send NOTHING — no blind action).
func _raiseFront(_ app: NSRunningApplication) -> Bool {
    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    for _ in 0..<15 {
        app.activate(options: [.activateAllWindows])
        if let w = (_axAttr(axApp, "AXWindows") as? [AXUIElement])?.first {
            AXUIElementPerformAction(w, "AXRaise" as CFString)
        }
        usleep(200_000)
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier { return true }
    }
    return false
}

// Select the conversation/project whose sidebar row matches `hint`. Strategy: if already focused
// (header shows the folder) -> done; else score sidebar rows (left 38%) by name match (FR-105:
// require a clear winner, gap >= 0.10) and click it; verify the header then shows the target.
// Returns (ok, frame, scale, status). ok=false means we could not PROVE the target -> caller alerts.
func _selectTarget(_ appName: String, _ hint: String) async -> (ok: Bool, frame: CGRect, scale: Double, status: String) {
    _rlog("selectTarget app=\(appName) hint='\(hint)'")
    guard AXIsProcessTrusted() else { _rlog("  -> AX-ERR (not trusted)"); return (false, .zero, 2, "AX-ERR") }
    guard let app = _runningApp(appName) else { _rlog("  -> ABORT-noproc"); return (false, .zero, 2, "ABORT-noproc") }
    if !_raiseFront(app) { _rlog("  -> ABORT-focus (never frontmost)"); return (false, .zero, 2, "ABORT-focus") }
    usleep(250_000)
    guard let cap = await _captureOCR(appName) else { _rlog("  -> CAPTURE-FAIL (Screen Recording?)"); return (false, .zero, 2, "CAPTURE-FAIL(grant Screen Recording)") }
    let (needles, eng) = _matchNeedles(hint)
    _rlog("  captured frame=\(Int(cap.frame.width))x\(Int(cap.frame.height)) runs=\(cap.runs.count) needles=\(needles) eng='\(eng)'")
    if needles.isEmpty { return (false, cap.frame, cap.scale, "NO-HINT") }
    if cap.runs.contains(where: { $0.ny < 0.12 && _nameMatches($0.text, needles, eng) }) {
        _rlog("  -> OK-already-focused (header match)"); return (true, cap.frame, cap.scale, "OK-already-focused")
    }
    // score sidebar rows (alias + romanized-fuzzy)
    let scored = cap.runs.filter { $0.nx < 0.38 }.map { ($0, _scoreRow($0, needles, eng)) }
        .filter { $0.1 >= 0.40 }.sorted { $0.1 > $1.1 }
    for c in scored.prefix(5) { _rlog("  cand score=\(String(format: "%.2f", c.1)) '\(c.0.text)'") }
    guard let best = scored.first else { _rlog("  -> NOT-FOUND (no sidebar row matched \(needles))"); return (false, cap.frame, cap.scale, "NOT-FOUND") }
    let gap = best.1 - (scored.count > 1 ? scored[1].1 : 0)
    if scored.count > 1 && gap < 0.10 { _rlog("  -> AMBIGUOUS_TARGET (gap \(String(format: "%.2f", gap)))"); return (false, cap.frame, cap.scale, "AMBIGUOUS_TARGET") }
    let pt = CGPoint(x: cap.frame.origin.x + best.0.cx / cap.scale,
                     y: cap.frame.origin.y + best.0.cy / cap.scale)
    _rlog("  click '\(best.0.text)' @ (\(Int(pt.x)),\(Int(pt.y)))")
    _mouseClick(pt)
    usleep(550_000)                                          // let the conversation load
    if let cap2 = await _captureOCR(appName), cap2.runs.contains(where: { $0.ny < 0.12 && _nameMatches($0.text, needles, eng) }) {
        _rlog("  -> OK (clicked + header verified)")
        return (true, cap2.frame, cap2.scale, "OK")
    }
    _rlog("  -> OK-clicked (header unverified)")
    return (true, cap.frame, cap.scale, "OK-clicked")        // clicked the matched row; header unverified
}

// "전환": just bring the right conversation to focus (NO typing). Doubles as a live targeting test.
func resolveFocus(appName: String, hint: String) async -> String {
    let r = await _selectTarget(appName, hint)
    return r.status
}

// Free banner poster (no self) so async focus tasks report without capturing the non-Sendable
// AppDelegate. Mirrors AppDelegate.postNotification; UNUserNotificationCenter.add is thread-safe.
func _postBanner(_ event: String, _ body: String) {
    let content = UNMutableNotificationContent()
    var title = event
    for p in ["nonya:", "노냐?:", "nonya :"] where title.lowercased().hasPrefix(p.lowercased()) {
        title = String(title.dropFirst(p.count)).trimmingCharacters(in: .whitespaces)
    }
    content.title = title.isEmpty ? "노냐?" : title
    content.body = body; content.sound = .default
    UNUserNotificationCenter.current().add(
        UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
}

// Map a _selectTarget status to a clear banner: success cue, or an actionable failure reason —
// so the user knows WHY nothing switched (the other half of "I can't tell what it's doing").
func _postFocusResult(_ status: String, _ label: String) {
    if status.hasPrefix("OK") { _postBanner(L10n.t("focus.ok.title"), label); return }
    let key: String
    if status.hasPrefix("CAPTURE-FAIL") { key = "focus.fail.capture" }
    else {
        switch status {
        case "AX-ERR": key = "focus.fail.ax"
        case "ABORT-noproc": key = "focus.fail.noproc"
        case "ABORT-focus": key = "focus.fail.focus"
        case "NOT-FOUND": key = "focus.fail.notfound"
        case "AMBIGUOUS_TARGET": key = "focus.fail.ambiguous"
        case "NO-HINT": key = "focus.fail.nohint"
        default: key = "focus.fail.generic"
        }
    }
    _postBanner(L10n.t("focus.fail.title") + " — " + label, L10n.t(key))
}

// Diagnostic only: capture+OCR the app window and dump every text run + the score it would get vs
// `hint`, split by region. NO raise, NO click, NO type — safe to run while the user works. Tells us
// exactly what Vision reads and why a match did/didn't happen (so matching can be fixed).
func resolveDebug(appName: String, hint: String) async -> String {
    guard _runningApp(appName) != nil else { return "no running app: \(appName)" }
    guard let cap = await _captureOCR(appName) else { return "CAPTURE-FAIL (Screen Recording not granted to this process?)" }
    let (needles, eng) = _matchNeedles(hint)
    var out = "frame=\(Int(cap.frame.width))x\(Int(cap.frame.height)) scale=\(cap.scale) runs=\(cap.runs.count) needles=\(needles) eng='\(eng)'\n"
    out += "HEADER (ny<0.12):\n"
    for r in cap.runs where r.ny < 0.12 {
        out += "  '\(r.text)' norm='\(_normName(r.text))' roman='\(_romanize(r.text))' match=\(_nameMatches(r.text, needles, eng))\n"
    }
    out += "SIDEBAR (nx<0.38), top scores:\n"
    let sb = cap.runs.filter { $0.nx < 0.38 }.map { ($0, _scoreRow($0, needles, eng)) }.sorted { $0.1 > $1.1 }
    for c in sb.prefix(25) {
        out += "  score=\(String(format: "%.2f", c.1)) cx=\(Int(c.0.cx)) cy=\(Int(c.0.cy)) norm='\(_normName(c.0.text))' roman='\(_romanize(c.0.text))' '\(c.0.text)'\n"
    }
    return out
}

// Full recovery: select the exact session, then type the nudge into its composer, READ IT BACK
// (re-OCR) to confirm it landed, and only THEN submit (FR-300/304/306). Never submits unverified.
// Find a leading chunk of `head` in the COMPOSER band (bottom of the window). Returns its screen
// point if present (i.e. the text is still sitting unsent in an input box) — also tells us WHICH
// composer (e.g. the right split pane) actually holds it, so Enter can be aimed there.
private func _composerText(_ appName: String, _ head: String) async -> (found: Bool, at: CGPoint?, ny: Double) {
    guard !head.isEmpty, let cap = await _captureOCR(appName) else { return (false, nil, 0) }
    // The composer is the INPUT box at the very BOTTOM. Pick the LOWEST (max-ny) occurrence and report
    // its ny, so a COPY of the same text sitting higher up — e.g. the just-sent message bubble — can be
    // told apart from text still in the input box (that confusion caused false "unsent" -> double submit).
    let hits = cap.runs.filter { $0.ny > 0.80 && _normName($0.text).contains(head) }
    if let r = hits.max(by: { $0.ny < $1.ny }) {
        return (true, CGPoint(x: cap.frame.origin.x + r.cx / cap.scale, y: cap.frame.origin.y + r.cy / cap.scale), r.ny)
    }
    return (false, nil, 0)
}

func resolveInject(appName: String, hint: String, text: String, sendKey: String) async -> String {
    let sel = await _selectTarget(appName, hint)
    if !sel.ok { return sel.status }                         // couldn't prove the target -> do not type
    let pb = NSPasteboard.general
    let prev = pb.string(forType: .string)
    func restore() { if let p = prev { pb.clearContents(); pb.setString(p, forType: .string) } }
    let src = CGEventSource(stateID: .combinedSessionState)
    func key(_ vk: CGKeyCode, _ cmd: Bool = false) {
        let d = CGEvent(keyboardEventSource: src, virtualKey: vk, keyDown: true); if cmd { d?.flags = .maskCommand }; d?.post(tap: .cghidEventTap)
        let u = CGEvent(keyboardEventSource: src, virtualKey: vk, keyDown: false); if cmd { u?.flags = .maskCommand }; u?.post(tap: .cghidEventTap)
    }
    // Claude/Codex desktop composers are Electron/WebKit: Cmd+Return SUBMITS, plain Return is a
    // NEWLINE. So LEAD with Cmd+Return (attempt 0) — leading with plain Return just typed a newline and
    // wasted the first ~1.5s, only submitting on attempt 1. Drop to plain Return ONCE (attempt 1) as a
    // fallback for the rare box where Return submits, then back to Cmd+Return. Explicit sendKey wins;
    // the capture-verify loop below confirms which key actually emptied the composer.
    func enter(_ attempt: Int) {
        let useCmd: Bool
        if sendKey == "cmd+return" { useCmd = true }
        else if sendKey == "return" { useCmd = (attempt != 1) }
        else { useCmd = attempt > 0 }
        key(0x24, useCmd)
    }
    let head = String(_normName(text).prefix(6))
    // 1) focus the composer (bottom input) and paste
    _mouseClick(CGPoint(x: sel.frame.origin.x + sel.frame.width * 0.6, y: sel.frame.origin.y + sel.frame.height * 0.94))
    usleep(250_000)
    pb.clearContents(); pb.setString(text, forType: .string)
    key(0x09, true)                                          // Cmd+V
    usleep(450_000)
    // 2) CAPTURE-VERIFY the text actually landed in a composer; this ALSO gives its exact location
    //    (which composer / which split pane holds it) so Enter is aimed at the right box.
    let typed = await _composerText(appName, head)
    if !typed.found { restore(); return "COMPOSER-VERIFY-FAIL" }
    let composerNy = typed.ny                                // where the text sits while IN the input box
    // 3) SUBMIT, then verify it left the INPUT BOX. After a real submit the text becomes the newest
    //    message bubble, which still sits low on screen — so "text is still somewhere at the bottom" is
    //    NOT proof it's unsent (that false negative double-submitted and then lied TYPED-NOT-SUBMITTED).
    //    It's SENT iff the text is gone OR its lowest occurrence moved UP out of the input box. At most
    //    TWO distinct keys (Cmd+Return, then plain Return) so a working composer is never spam-submitted.
    var submitted = false
    for attempt in 0..<2 {
        if let at = typed.at { _mouseClick(at); usleep(120_000) }   // re-aim at the box that held our text
        enter(attempt)                                       // attempt 0 Cmd+Return, attempt 1 plain Return
        usleep(700_000)                                      // capture interval — let the send settle
        let still = await _composerText(appName, head)
        if !still.found || still.ny < composerNy - 0.03 {    // gone, or moved up into the conversation -> SENT
            submitted = true; break
        }
        _rlog("resolveInject: attempt \(attempt + 1) — still in input box (ny \(still.ny) vs \(composerNy)), re-sending")
    }
    restore()
    return submitted ? "OK" : "TYPED-NOT-SUBMITTED"          // caller keeps retrying if not sent
}

// VISUAL state sensor (no model — a game-macro style capture+OCR check): read the app window and
// classify what's ON SCREEN. error (server/overload/retry/limit) > working (generating/stop) > idle.
// Capture only (no raise/click/type) — safe to poll at intervals to confirm a session's real state.
func resolveState(appName: String, hint: String = "") async -> String {
    guard _runningApp(appName) != nil else { return "no-app" }
    guard let cap = await _captureOCR(appName) else { return "CAPTURE-FAIL" }
    let blob = cap.runs.map { _normName($0.text) }.joined(separator: " ")
    let errMarks = ["서버오류", "서비스가사용", "다시시도", "overloaded", "오류가발생", "ratelimit",
                    "사용한도", "usagelimit", "내부오류", "잠시후다시", "serviceisbusy"]
    if errMarks.contains(where: { blob.contains($0) }) { return "error" }
    let workMarks = ["생성중", "중지", "응답중", "stopgenerating", "stopstreaming", "thinking", "작업중"]
    if workMarks.contains(where: { blob.contains($0) }) { return "working" }
    return "idle"
}

// Diagnostic: dump a terminal app's AX tree (roles + value snippets) + whether THIS process is
// AX-trusted. Used to settle "does Terminal.app expose its scrollback as AXTextArea?" empirically.
func axDump(appName: String, needle: String = "") {
    print("AXIsProcessTrusted=\(AXIsProcessTrusted())")
    guard let app = _runningApp(appName) else { print("NO APP MATCH: \(appName)"); return }
    print("matched: localizedName=\(app.localizedName ?? "?") exe=\(app.executableURL?.lastPathComponent ?? "?") bid=\(app.bundleIdentifier ?? "?")")
    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetAttributeValue(axApp, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    usleep(400_000)
    if !needle.isEmpty {                                   // count AXTextAreas whose FULL value contains needle
        var areas: [AXUIElement] = []
        for w in (_axAttr(axApp, "AXWindows") as? [AXUIElement]) ?? [] { _axTextAreas(w, 0, &areas) }
        let n = areas.filter { ((_axAttr($0, "AXValue") as? String) ?? "").lowercased().contains(needle.lowercased()) }.count
        print("NEEDLE '\(needle)' found in \(n) of \(areas.count) AXTextAreas")
    }
    var count = 0
    func walk(_ e: AXUIElement, _ d: Int) {
        if d > 12 || count > 400 { return }
        count += 1
        let role = (_axAttr(e, "AXRole") as? String) ?? "?"
        let val = (_axAttr(e, "AXValue") as? String) ?? ""
        let snip = String(val.replacingOccurrences(of: "\n", with: "\\n").prefix(220))
        print(String(repeating: "  ", count: d) + role + (snip.isEmpty ? "" : "  val=[\(snip)]"))
        if let kids = _axAttr(e, "AXChildren") as? [AXUIElement] { for c in kids { walk(c, d + 1) } }
    }
    let wins = (_axAttr(axApp, "AXWindows") as? [AXUIElement]) ?? []
    print("windows=\(wins.count)")
    for w in wins { walk(w, 0) }
}

// For the read-only briefing, prefer the python launcher (~0.08s) over the PyInstaller
// bundled core (~3.8s cold-start unpack) so the window fills near-instantly.
func briefingBinary() -> (URL, [String]) {
    let dev = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/bin/nonya").path
    if FileManager.default.isExecutableFile(atPath: dev) { return (URL(fileURLWithPath: dev), []) }
    return nonyaBinary()
}

// MARK: - status-bar eyes (collision-proof: macOS manages the slot, never overlaps menus/notch/other apps)

enum EyeStyle: String, CaseIterable {
    case neon, mecha, anime, predator, minimal
    var label: String { L10n.t("eyestyle." + rawValue) }
    static let defaultsKey = "nonya.eyeStyle"
    static var selected: EyeStyle { EyeStyle(rawValue: UserDefaults.standard.string(forKey: defaultsKey) ?? "") ?? .neon }
    static func choose(_ s: EyeStyle) { UserDefaults.standard.set(s.rawValue, forKey: defaultsKey) }
}

final class EyesView: NSView {
    var mood = "watching" { didSet { needsDisplay = true } }
    var style: EyeStyle = EyeStyle.selected { didSet { needsDisplay = true } }
    private var blink: CGFloat = 0, nextBlink: CGFloat = 1.5, t: CGFloat = 0
    private var gx: CGFloat = 0, gy: CGFloat = 0, tgx: CGFloat = 0, tgy: CGFloat = 0, nextDart: CGFloat = 1.2
    private var lid: CGFloat = 0.8, breath: CGFloat = 0, peek: CGFloat = 0, nextPeek: CGFloat = 4, glow: CGFloat = 1
    private var timer: Timer?
    // Does this mood follow the cursor? Any mood with a LIVE session tracks the mouse so the
    // face always feels alive (the color/lid/pulse convey the state, not whether it's frozen).
    // Only the genuinely-inactive moods rest still: asleep (idle), session ended (stopped/done).
    var tracksCursor: Bool {
        switch mood { case "idle", "stopped", "done": return false; default: return true }
    }
    // per-mood eye behavior: (restingLid 0=open..1=shut, breathe, dart=idle micro-saccades, pulse glow, _)
    // NOTE: cursor-following is governed by tracksCursor above; `dart` only adds idle wandering
    // when the mouse is still (lively moods). Attention moods follow the mouse but don't self-wander.
    private func moodParams() -> (CGFloat, Bool, Bool, Bool, Bool) {
        switch mood {
        case "idle":               return (0.93, true,  false, false, false)  // asleep: closed lids, breathing + drowsy peeks
        case "stopped":            return (0.74, false, false, false, false)  // dim, half-shut, still
        case "done":               return (0.95, false, false, false, false)  // closed happy arc ^_^
        case "stuck", "scolding":  return (0.42, false, false, true,  false)  // narrowed glare, follows you, pulse
        case "rate-limited":       return (0.62, false, false, true,  false)  // half-shut + slow pulse: blocked, waiting to resume
        case "waiting":            return (-0.08, false, false, true, false)  // wide, follows you, pulse: needs YOU
        case "looping":            return (0.0,  false, true,  true,  false)  // open + idle wander + anomaly pulse
        default:                   return (0.0,  false, true,  false, false)  // watching/working: open + track + blink
        }
    }
    override init(frame: NSRect) { super.init(frame: frame); common() }
    required init?(coder: NSCoder) { super.init(coder: coder); common() }
    private func common() {
        wantsLayer = true
        timer = Timer.scheduledTimer(withTimeInterval: 1.0/30.0, repeats: true) { [weak self] _ in self?.step() }
    }
    func setGaze(_ dx: CGFloat, _ dy: CGFloat) { tgx = max(-1, min(1, dx)); tgy = max(-1, min(1, dy)); nextDart = 2.0 }
    // settle to a representative still pose for offscreen state-preview rendering
    func poseForRender() {
        let (rest, _, _, pulse, _) = moodParams()
        lid = max(0, min(0.98, rest)); blink = 0; peek = 0; breath = 0.9   // mid-high breath -> show the glow
        glow = pulse ? 0.7 : 1.0
        gx = tracksCursor ? 0.32 : 0; gy = tracksCursor ? 0.18 : 0
        needsDisplay = true
    }
    private func step() {
        let dt: CGFloat = 1.0/30.0; t += dt
        let (rest, breathe, track, pulse, _) = moodParams()
        breath += dt
        var base = rest
        if breathe {                                    // asleep: gentle breathing + the odd drowsy peek
            base += sin(breath * 1.5) * 0.07
            nextPeek -= dt; if nextPeek <= 0 { peek = 1; nextPeek = 5 + CGFloat.random(in: 0...4) }
            peek = max(0, peek - dt * 1.6); base -= peek * 0.55
        }
        if track {                                      // alive blink only while eyes are open + tracking
            nextBlink -= dt; if nextBlink <= 0 { blink = 1; nextBlink = 2 + CGFloat.random(in: 0...3) }
        }
        blink = max(0, blink - dt * 8)
        let targetLid = max(-0.1, min(1, base + blink))
        lid += (targetLid - lid) * 0.30                 // smooth -> eyes "wake up"/"fall asleep" on state change
        if tracksCursor {                               // any live session: the gaze follows the mouse (hover sets tgx/tgy)
            nextDart -= dt
            if track && nextDart <= 0 {                 // lively moods also idly wander when the mouse is still
                tgx = CGFloat.random(in: -0.6...0.6); tgy = CGFloat.random(in: -0.5...0.5); nextDart = 1 + CGFloat.random(in: 0...2)
            }
            gx += (tgx - gx) * 0.15; gy += (tgy - gy) * 0.15
        } else { gx *= 0.85; gy *= 0.85 }               // asleep/ended: relax to center
        let glowTarget: CGFloat = pulse ? (0.4 + 0.6 * (0.5 + 0.5 * sin(t * 5))) : 1.0
        glow += (glowTarget - glow) * 0.3
        needsDisplay = true
    }
    private func irisColor() -> NSColor {
        switch mood {
        case "scolding": return NSColor(srgbRed: 1.0, green: 0.70, blue: 0.23, alpha: 1)
        case "stuck":    return NSColor(srgbRed: 1.0, green: 0.27, blue: 0.20, alpha: 1)
        case "waiting":  return NSColor(srgbRed: 1.0, green: 0.85, blue: 0.30, alpha: 1)   // needs YOU (a question/permission)
        case "looping":  return NSColor(srgbRed: 0.74, green: 0.45, blue: 1.0,  alpha: 1)   // anomaly: repeating itself
        case "rate-limited": return NSColor(srgbRed: 0.95, green: 0.62, blue: 0.18, alpha: 1)  // amber: blocked, throttled — not idle
        case "working", "done": return NSColor(srgbRed: 0.36, green: 0.88, blue: 0.56, alpha: 1)
        case "stopped":  return NSColor(srgbRed: 0.55, green: 0.57, blue: 0.62, alpha: 1)   // dim grey: session ended
        case "idle":     return NSColor(srgbRed: 0.52, green: 0.66, blue: 0.84, alpha: 1)   // cool slate-cyan: dormant, breathing
        default:         return NSColor(srgbRed: 0.44, green: 0.90, blue: 0.84, alpha: 1)
        }
    }
    // --- drawing helpers ---
    private let W = NSColor(srgbRed: 1, green: 1, blue: 1, alpha: 1)
    private let K = NSColor(srgbRed: 0.02, green: 0.02, blue: 0.03, alpha: 1)
    private func mix(_ c: NSColor, _ f: CGFloat, _ o: NSColor) -> NSColor { c.blended(withFraction: f, of: o) ?? c }
    private func radial(_ ctx: CGContext, _ ctr: CGPoint, _ r: CGFloat, _ cols: [NSColor], _ locs: [CGFloat]) {
        let sp = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        guard let g = CGGradient(colorsSpace: sp, colors: cols.map { $0.cgColor } as CFArray, locations: locs) else { return }
        ctx.drawRadialGradient(g, startCenter: ctr, startRadius: 0, endCenter: ctr, endRadius: r, options: [.drawsAfterEndLocation])
    }
    private func linear(_ ctx: CGContext, _ a: CGPoint, _ b: CGPoint, _ cols: [NSColor], _ locs: [CGFloat]) {
        let sp = CGColorSpace(name: CGColorSpace.sRGB) ?? CGColorSpaceCreateDeviceRGB()
        guard let g = CGGradient(colorsSpace: sp, colors: cols.map { $0.cgColor } as CFArray, locations: locs) else { return }
        ctx.drawLinearGradient(g, start: a, end: b, options: [])
    }

    override func draw(_ dirty: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        let b = bounds
        // fit both dimensions: a tall card must not blow the eyes past its width (else they clip)
        let ry0 = min(b.height * 0.38, b.width * 0.205), rx = ry0 * 1.05, cy = b.midY, gap = rx * 2.35
        let color = irisColor().withAlphaComponent(max(0.35, min(1, glow)))   // pulse = attention (waiting/stuck/looping)
        let shut = max(0, min(0.98, lid))
        let cyB = breath > 0 && mood == "idle" ? cy + sin(breath * 1.5) * (ry0 * 0.10) : cy   // sleeping bob
        for cx in [b.midX - gap/2, b.midX + gap/2] {
            let ry = ry0 * (1 - shut * 0.94)             // lid = vertical squash; near-shut -> a closed arc
            let ix = cx + gx * rx * 0.32, iy = cyB + gy * ry0 * 0.30
            ctx.saveGState()
            if ry < ry0 * 0.18 {                          // eyes closed: draw a lid arc so the icon is never blank
                if mood == "idle" { drawSleeping(ctx, cx, cyB, rx, irisColor(), cx < b.midX ? -1 : 1) }
                else { drawClosed(ctx, cx, cyB, rx, color, mood == "done") }
            } else {
                switch style {
                case .neon:     drawNeon(ctx, cx, cyB, rx, ry, ix, iy, color)
                case .mecha:    drawMecha(ctx, cx, cyB, rx, ry, ix, iy, color)
                case .anime:    drawAnime(ctx, cx, cyB, rx, ry, ix, iy, color)
                case .predator: drawPredator(ctx, cx, cyB, rx, ry, ix, iy, color)
                case .minimal:  drawMinimal(ctx, cx, cyB, rx, ry, ix, iy, color)
                }
            }
            ctx.restoreGState()
        }
    }

    // dormant "sleeping" eye: breathing underglow (a nod to the Mac sleep LED) + a glossy
    // closed crescent + a lash flick. Calm + alive — unmistakably "not watching".
    private func drawSleeping(_ ctx: CGContext, _ cx: CGFloat, _ cy: CGFloat, _ rx: CGFloat, _ base: NSColor, _ dir: CGFloat) {
        let br = 0.5 + 0.5 * sin(breath * 1.4)                       // slow breath 0..1
        let glowC = mix(base, 0.4, NSColor(srgbRed: 0.45, green: 0.85, blue: 1.0, alpha: 1))
        ctx.saveGState()                                            // 1) breathing halo
        radial(ctx, CGPoint(x: cx, y: cy), rx * (1.05 + 0.75 * br),
               [glowC.withAlphaComponent(0.07 + 0.30 * br), glowC.withAlphaComponent(0)], [0, 1])
        ctx.restoreGState()
        let w = rx * 0.97, dip = rx * 0.34                          // 2) glossy closed crescent (calm ⌒)
        let p = CGMutablePath()
        p.move(to: CGPoint(x: cx - w, y: cy + dip * 0.34))
        p.addQuadCurve(to: CGPoint(x: cx + w, y: cy + dip * 0.34), control: CGPoint(x: cx, y: cy - dip))
        ctx.saveGState(); ctx.setLineCap(.round)
        ctx.setShadow(offset: .zero, blur: rx * (0.5 + 0.7 * br), color: glowC.withAlphaComponent(0.55 + 0.45 * br).cgColor)
        ctx.setStrokeColor(base.withAlphaComponent(0.55).cgColor); ctx.setLineWidth(max(2.0, rx * 0.36)); ctx.addPath(p); ctx.strokePath()
        ctx.setShadow(offset: .zero, blur: 0, color: NSColor.clear.cgColor)
        ctx.setStrokeColor(mix(base, 0.55, W).withAlphaComponent(0.95).cgColor); ctx.setLineWidth(max(1.0, rx * 0.15)); ctx.addPath(p); ctx.strokePath()
        ctx.restoreGState()
        ctx.saveGState(); ctx.setLineCap(.round)                    // 3) outer lash flick
        ctx.setStrokeColor(base.withAlphaComponent(0.8).cgColor); ctx.setLineWidth(max(1.0, rx * 0.13))
        let ox = cx + dir * w                                       // lash on the OUTER corner of each eye
        let lp = CGMutablePath()
        lp.move(to: CGPoint(x: ox, y: cy + dip * 0.34)); lp.addLine(to: CGPoint(x: ox + dir * rx * 0.24, y: cy + dip * 0.62))
        ctx.addPath(lp); ctx.strokePath(); ctx.restoreGState()
    }

    // closed eye: a soft rounded arc — drooping (asleep/stopped) or upturned ^_^ (done)
    private func drawClosed(_ ctx: CGContext, _ cx: CGFloat, _ cy: CGFloat, _ rx: CGFloat, _ color: NSColor, _ happy: Bool) {
        let w = rx * 0.92
        let p = CGMutablePath()
        p.move(to: CGPoint(x: cx - w, y: cy))
        p.addQuadCurve(to: CGPoint(x: cx + w, y: cy), control: CGPoint(x: cx, y: cy + (happy ? rx * 0.6 : -rx * 0.24)))
        ctx.setLineCap(.round); ctx.setLineWidth(max(1.6, rx * 0.34))
        ctx.setStrokeColor(color.cgColor)
        ctx.setShadow(offset: .zero, blur: rx * 0.5, color: color.withAlphaComponent(0.7).cgColor)
        ctx.addPath(p); ctx.strokePath()
    }

    private func drawNeon(_ ctx: CGContext, _ cx: CGFloat, _ cy: CGFloat, _ rx: CGFloat, _ ry: CGFloat, _ ix: CGFloat, _ iy: CGFloat, _ color: NSColor) {
        let lens = CGRect(x: cx-rx, y: cy-ry, width: rx*2, height: ry*2)
        ctx.saveGState(); ctx.addEllipse(in: lens); ctx.clip()
        radial(ctx, CGPoint(x: cx, y: cy+ry*0.2), rx*1.2, [NSColor(white: 0.13, alpha: 1), K], [0, 1])
        ctx.restoreGState()
        let ir = ry * 0.74
        ctx.saveGState()
        ctx.setShadow(offset: .zero, blur: ir*0.8, color: color.cgColor)
        ctx.setStrokeColor(color.cgColor); ctx.setLineWidth(max(1.4, ir*0.30))
        ctx.strokeEllipse(in: CGRect(x: ix-ir, y: iy-ir, width: ir*2, height: ir*2))
        ctx.restoreGState()
        ctx.saveGState(); ctx.addEllipse(in: CGRect(x: ix-ir*0.55, y: iy-ir*0.55, width: ir*1.1, height: ir*1.1)); ctx.clip()
        radial(ctx, CGPoint(x: ix, y: iy), ir*0.55, [mix(color, 0.7, W), color], [0, 1]); ctx.restoreGState()
        K.setFill(); ctx.fillEllipse(in: CGRect(x: ix-ir*0.24, y: iy-ir*0.24, width: ir*0.48, height: ir*0.48))
        W.withAlphaComponent(0.95).setFill(); ctx.fillEllipse(in: CGRect(x: ix-ir*0.34, y: iy+ir*0.14, width: ir*0.22, height: ir*0.22))
    }

    private func drawMecha(_ ctx: CGContext, _ cx: CGFloat, _ cy: CGFloat, _ rx: CGFloat, _ ry: CGFloat, _ ix: CGFloat, _ iy: CGFloat, _ color: NSColor) {
        let r = CGRect(x: cx-rx, y: cy-ry, width: rx*2, height: ry*2)
        let path = CGPath(roundedRect: r, cornerWidth: ry*0.28, cornerHeight: ry*0.28, transform: nil)
        ctx.saveGState(); ctx.addPath(path); ctx.clip()
        NSColor(white: 0.05, alpha: 1).setFill(); ctx.fill(r)
        ctx.setStrokeColor(color.withAlphaComponent(0.20).cgColor); ctx.setLineWidth(0.8)
        var yy = cy - ry; while yy < cy + ry { ctx.move(to: CGPoint(x: cx-rx, y: yy)); ctx.addLine(to: CGPoint(x: cx+rx, y: yy)); yy += 2.4 }
        ctx.strokePath()
        ctx.setShadow(offset: .zero, blur: ry*0.6, color: color.cgColor)
        color.setFill(); ctx.fill(CGRect(x: ix-rx*0.46, y: iy-ry*0.30, width: rx*0.92, height: ry*0.60))
        ctx.restoreGState()
        ctx.addPath(path); ctx.setStrokeColor(color.cgColor); ctx.setLineWidth(max(1.2, ry*0.13)); ctx.strokePath()
    }

    private func drawAnime(_ ctx: CGContext, _ cx: CGFloat, _ cy: CGFloat, _ rx: CGFloat, _ ry: CGFloat, _ ix: CGFloat, _ iy: CGFloat, _ color: NSColor) {
        let er = CGRect(x: cx-rx, y: cy-ry, width: rx*2, height: ry*2)
        ctx.saveGState(); ctx.addEllipse(in: er); ctx.clip()
        W.setFill(); ctx.fill(er)
        let ir = ry * 0.96
        ctx.addEllipse(in: CGRect(x: ix-ir*0.82, y: iy-ir, width: ir*1.64, height: ir*2)); ctx.clip()
        linear(ctx, CGPoint(x: ix, y: iy+ir), CGPoint(x: ix, y: iy-ir), [mix(color, 0.45, W), color, mix(color, 0.55, K)], [0, 0.5, 1])
        K.setFill(); ctx.fillEllipse(in: CGRect(x: ix-ir*0.46, y: iy-ir*0.5, width: ir*0.92, height: ir*0.92))
        W.withAlphaComponent(0.95).setFill(); ctx.fillEllipse(in: CGRect(x: ix-ir*0.5, y: iy+ir*0.12, width: ir*0.5, height: ir*0.5))
        W.withAlphaComponent(0.7).setFill(); ctx.fillEllipse(in: CGRect(x: ix+ir*0.18, y: iy-ir*0.42, width: ir*0.24, height: ir*0.24))
        ctx.restoreGState()
    }

    private func drawPredator(_ ctx: CGContext, _ cx: CGFloat, _ cy: CGFloat, _ rx: CGFloat, _ ry: CGFloat, _ ix: CGFloat, _ iy: CGFloat, _ color: NSColor) {
        let p = CGMutablePath()
        p.move(to: CGPoint(x: cx-rx, y: cy))
        p.addQuadCurve(to: CGPoint(x: cx+rx, y: cy), control: CGPoint(x: cx, y: cy+ry*1.5))
        p.addQuadCurve(to: CGPoint(x: cx-rx, y: cy), control: CGPoint(x: cx, y: cy-ry*1.5))
        ctx.saveGState(); ctx.addPath(p); ctx.clip()
        radial(ctx, CGPoint(x: ix, y: iy), rx, [mix(color, 0.45, W), mix(color, 0.40, K)], [0, 1])
        K.setFill(); ctx.fillEllipse(in: CGRect(x: ix-rx*0.12, y: iy-ry*0.92, width: rx*0.24, height: ry*1.84))
        W.withAlphaComponent(0.85).setFill(); ctx.fillEllipse(in: CGRect(x: ix-rx*0.34, y: iy+ry*0.28, width: rx*0.20, height: ry*0.30))
        ctx.restoreGState()
        ctx.addPath(p); ctx.setStrokeColor(color.withAlphaComponent(0.85).cgColor); ctx.setLineWidth(1.3); ctx.strokePath()
    }

    private func drawMinimal(_ ctx: CGContext, _ cx: CGFloat, _ cy: CGFloat, _ rx: CGFloat, _ ry: CGFloat, _ ix: CGFloat, _ iy: CGFloat, _ color: NSColor) {
        let er = CGRect(x: cx-rx, y: cy-ry, width: rx*2, height: ry*2)
        let path = CGPath(roundedRect: er, cornerWidth: min(rx, ry), cornerHeight: min(rx, ry), transform: nil)
        ctx.saveGState(); ctx.addPath(path); ctx.clip()
        radial(ctx, CGPoint(x: ix, y: iy+ry*0.3), rx*1.5, [mix(color, 0.40, W), mix(color, 0.50, K)], [0, 1])
        W.withAlphaComponent(0.32).setFill(); ctx.fillEllipse(in: CGRect(x: cx-rx*0.62, y: cy+ry*0.22, width: rx*1.24, height: ry*0.62))
        ctx.restoreGState()
    }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }   // let the status button receive clicks
    deinit { timer?.invalidate() }
}

// a clickable card showing one eye style in the preview picker
final class EyeCard: NSView {
    private let onClick: (EyeCard) -> Void
    init(frame: NSRect, onClick: @escaping (EyeCard) -> Void) { self.onClick = onClick; super.init(frame: frame); wantsLayer = true }
    required init?(coder: NSCoder) { fatalError() }
    override func mouseDown(with event: NSEvent) { onClick(self) }
}

// MARK: - i18n (menu chrome; the core localizes its own runtime notifications in 9 langs)

enum L10n {
    // (code, native display name) — "auto" follows the OS locale
    static let choices: [(String, String)] = [
        ("auto", "Auto (system)"), ("en", "English"), ("ko", "한국어"), ("ja", "日本語"),
        ("zh-Hans", "简体中文"), ("zh-Hant", "繁體中文"), ("es", "Español"),
        ("fr", "Français"), ("de", "Deutsch"), ("pt-BR", "Português (BR)"),
    ]
    static var pref: String { UserDefaults.standard.string(forKey: "nonya.lang") ?? "auto" }
    static var effective: String {
        let p = pref
        if p != "auto" { return p }
        let sys = (Locale.preferredLanguages.first ?? "en").lowercased()
        for code in ["ko", "ja", "zh-hant", "zh-hans", "zh", "es", "fr", "de", "pt"] where sys.hasPrefix(code) {
            if code == "zh" { return "zh-Hans" }
            if code == "zh-hant" { return "zh-Hant" }
            if code == "zh-hans" { return "zh-Hans" }
            if code == "pt" { return "pt-BR" }
            return code
        }
        return "en"
    }
    static func t(_ k: String) -> String { (M[effective]?[k]) ?? (M["en"]?[k]) ?? k }
    static let M: [String: [String: String]] = [
        "en": ["stop": "Stop", "watch.claude.app": "Watch Claude (app)", "watch.claude.cli": "Watch Claude (CLI)",
               "watch.codex.app": "Watch Codex (app)", "watch.codex.cli": "Watch Codex (CLI)", "watch.all": "Watch all apps",
               "launch.claude": "Launch Claude in tmux (safe recovery)", "launch.codex": "Launch Codex in tmux (safe recovery)",
               "menu.hdr.watch": "WATCH — which sessions", "menu.hdr.launch": "START in tmux (safe recovery)",
               "menu.hdr.mode": "INTERVENTION — how nonya acts",
               "menu.hdr.sessions": "WATCHING — active / needs attention", "sess.none": "(no active sessions need attention)",
               "sess.alertonly.tip": " not on the tmux direct path. GUI recovery is conditional; use “Start in tmux” for deterministic recovery.",
               "st.working": "working", "st.watching": "watching", "st.waiting": "waiting for you",
               "st.stuck": "stuck", "st.looping": "repeating", "st.rate-limited": "rate-limited",
               "st.done": "done", "st.stopped": "stopped", "st.idle": "idle",
               "st.keep-going": "keep-going", "st.injected": "injected", "st.recovered": "recovered",
               "st.resolved": "resolved", "st.escalated": "escalated", "st.alert-only": "alert-only",
               "st.shadow": "shadow", "st.dry-run": "dry-run", "st.no-inject": "no-inject",
               "st.no-effect": "no-effect", "st.preview-cancelled": "cancelled",
               "mode.onerror": "Only on errors / stalls (default)",
               "mode.auto.full": "Autonomous — continue explicit <<DONE>> work",
               "mode.status.auto": "Intervention: Autonomous (overnight)", "mode.status.onerror": "Intervention: only on errors/stalls",
               "mode.overnight": "Autonomous mode (overnight)", "briefing": "Night briefing",
               "selftest": "Self-test recovery (prove it works)", "selftest.running": "Running recovery self-test…",
               "selftest.title": "nonya — recovery self-test",
               "injecttest": "Inject test → focused Claude (raise + send)", "injecttest.title": "nonya — live inject test",
               "metrics": "Intervention metrics", "metrics.title": "nonya — intervention metrics",
               "watch.start": "Start watching", "metrics.range": "Intervention metrics — range",
               "metrics.win.12": "last 12h", "metrics.win.24": "last 24h", "metrics.win.48": "last 48h", "metrics.win.all": "all time",
               "m.interventions": "interventions", "m.delivery": "delivery", "m.safety": "safety",
               "m.keys": "keys sent", "m.recovered": "recovered", "m.shadow": "shadow",
               "m.byclass": "BY CLASS", "m.byoutcome": "BY OUTCOME", "m.chain": "ledger chain",
               "m.ok": "OK", "m.violated": "VIOLATED", "m.linked": "linked", "m.broken": "BROKEN",
               "m.none": "No interventions yet. Turn on Shadow + Watch all (in tmux) and let it run.",
               "m.log": "WHEN · SESSION · CLASS → OUTCOME (what was sent)", "m.logempty": "(no interventions logged yet)",
               "mode.shadow": "Shadow — decide & record only, send no keys",
               "eyestyles": "Eye style", "perms": "Open permission settings", "quit": "Quit", "language": "Language",
               "eyestyle.neon": "Neon Cyber", "eyestyle.mecha": "Mecha HUD", "eyestyle.anime": "Anime Glossy",
               "eyestyle.predator": "Predator Slit", "eyestyle.minimal": "Minimal Lens",
               "win.briefing": "노냐? — night briefing", "win.eyestyles": "Eye style", "briefing.error": "Could not load the briefing.",
               "settings": "Settings", "set.sound": "Sound (chime on nudge)", "set.mode": "Intervene",
               "set.default": "Default", "set.preview": "Preview before inject (sec)", "set.idle": "Idle before acting (sec)",
               "set.character": "Character", "set.slack": "Slack webhook", "set.tgtoken": "Telegram bot token",
               "set.tgchat": "Telegram chat id", "set.ntfy": "ntfy topic", "set.note": "Changes apply immediately to running watches.",
               "set.autoupdate": "Auto-update from GitHub", "update.check": "Check for updates",
               "update.uptodate.title": "Up to date", "update.uptodate.body": "You're on the latest version",
               "update.available.title": "Update available", "update.available.body": "New version:",
               "update.downloading.title": "Updating", "update.downloading.body": "Downloading & installing",
               "update.fail.title": "Update check failed", "update.fail.body": "Couldn't reach GitHub. Try again later.",
               "preview.title": "nonya — inject preview", "preview.inject": "Inject now", "preview.cancel": "Cancel", "preview.count": "auto-injects in",
               "focus.run.title": "Switching…", "focus.run.body": "Bringing to front:",
               "focus.ok.title": "Switched", "focus.fail.title": "Switch failed",
               "focus.fail.notfound": "Couldn't find that project in the sidebar. Add an alias in ~/.local/state/nonya/aliases.json.",
               "focus.fail.ambiguous": "Several rows looked alike — couldn't pick one safely.",
               "focus.fail.capture": "Screen Recording permission needed (System Settings → Privacy → Screen Recording).",
               "focus.fail.ax": "Accessibility permission needed (System Settings → Privacy → Accessibility).",
               "focus.fail.noproc": "The target app isn't running.",
               "focus.fail.focus": "Couldn't bring the app to the front.",
               "focus.fail.nohint": "No name to match.", "focus.fail.generic": "Couldn't confirm the target.",
               "briefing.loading": "Loading the night briefing…", "briefing.title": "노냐? — night briefing"],
        "ko": ["stop": "정지", "watch.claude.app": "Claude 앱 감시", "watch.claude.cli": "Claude CLI 감시",
               "watch.codex.app": "Codex 앱 감시", "watch.codex.cli": "Codex CLI 감시", "watch.all": "모든 앱 감시",
               "launch.claude": "Claude를 tmux에서 시작 (안전 복구)", "launch.codex": "Codex를 tmux에서 시작 (안전 복구)",
               "menu.hdr.watch": "감시 대상 — 무엇을 볼지", "menu.hdr.launch": "tmux에서 안전 시작",
               "menu.hdr.mode": "개입 방식 — 어떻게 행동할지",
               "menu.hdr.sessions": "감시 중 — 활성·확인 필요", "sess.none": "(확인할 활성 세션 없음)",
               "sess.alertonly.tip": "개 세션은 tmux 직접복구 아님 — 앱은 조건부, 확정 복구는 ‘tmux에서 시작’",
               "st.working": "작업 중", "st.watching": "감시 중", "st.waiting": "입력 대기",
               "st.stuck": "막힘", "st.looping": "같은 작업 반복", "st.rate-limited": "레이트리밋",
               "st.done": "완료", "st.stopped": "종료됨", "st.idle": "유휴",
               "st.keep-going": "계속 진행", "st.injected": "주입함", "st.recovered": "복구됨",
               "st.resolved": "해결됨", "st.escalated": "에스컬레이션", "st.alert-only": "알림만",
               "st.shadow": "그림자", "st.dry-run": "드라이런", "st.no-inject": "주입안함",
               "st.no-effect": "무반응", "st.preview-cancelled": "취소됨",
               "mode.onerror": "오류·멈춤일 때만 개입 (기본)",
               "mode.auto.full": "자율 모드 — 요청된 <<DONE>> 작업만 계속",
               "mode.status.auto": "개입 방식: 자율 (밤샘)", "mode.status.onerror": "개입 방식: 오류·멈춤일 때만",
               "mode.overnight": "자율 모드 (밤샘)", "briefing": "야간 브리핑 보기",
               "injecttest": "주입 테스트 → 앞 Claude에 입력·전송", "injecttest.title": "노냐? — 실주입 테스트",
               "selftest": "복구 자가진단 (정말 되는지 확인)", "selftest.running": "복구 자가진단 실행 중…",
               "selftest.title": "노냐? — 복구 자가진단",
               "metrics": "개입 지표 보기", "metrics.title": "노냐? — 개입 지표",
               "watch.start": "감시 시작", "metrics.range": "개입 지표 — 기간",
               "metrics.win.12": "최근 12시간", "metrics.win.24": "최근 24시간", "metrics.win.48": "최근 48시간", "metrics.win.all": "전체",
               "m.interventions": "개입", "m.delivery": "전달률", "m.safety": "안전",
               "m.keys": "키 전송", "m.recovered": "복구", "m.shadow": "그림자",
               "m.byclass": "분류별", "m.byoutcome": "결과별", "m.chain": "원장 체인",
               "m.ok": "정상", "m.violated": "위반", "m.linked": "무결", "m.broken": "끊김",
               "m.none": "아직 개입 기록 없음. 그림자 모드 + 모든 앱 감시(tmux)를 켜고 둬보세요.",
               "m.log": "시각 · 세션 · 분류 → 결과 (보낸 내용)", "m.logempty": "(아직 개입 기록 없음)",
               "mode.shadow": "그림자 모드 — 판단·기록만, 주입 안 함",
               "eyestyles": "눈 스타일", "perms": "권한 설정 열기", "quit": "종료", "language": "언어",
               "eyestyle.neon": "네온 사이버", "eyestyle.mecha": "메카 HUD", "eyestyle.anime": "애니 글로시",
               "eyestyle.predator": "포식자 슬릿", "eyestyle.minimal": "미니멀 렌즈",
               "win.briefing": "노냐? — 야간 브리핑", "win.eyestyles": "눈 스타일", "briefing.error": "브리핑을 불러올 수 없습니다.",
               "settings": "설정", "set.sound": "소리 (넛지 시 효과음)", "set.mode": "개입 모드",
               "set.default": "기본", "set.preview": "주입 전 미리보기 (초)", "set.idle": "개입 전 유휴 (초)",
               "set.character": "캐릭터", "set.slack": "Slack 웹훅", "set.tgtoken": "Telegram 봇 토큰",
               "set.tgchat": "Telegram chat id", "set.ntfy": "ntfy 토픽", "set.note": "변경은 실행 중인 감시에 즉시 적용됩니다.",
               "set.autoupdate": "GitHub 자동 업데이트", "update.check": "업데이트 확인",
               "update.uptodate.title": "최신 버전", "update.uptodate.body": "이미 최신 버전입니다",
               "update.available.title": "업데이트 있음", "update.available.body": "새 버전:",
               "update.downloading.title": "업데이트 중", "update.downloading.body": "내려받아 설치 중",
               "update.fail.title": "업데이트 확인 실패", "update.fail.body": "GitHub에 연결하지 못했어요. 잠시 후 다시.",
               "preview.title": "nonya — 주입 미리보기", "preview.inject": "지금 주입", "preview.cancel": "취소", "preview.count": "자동 주입까지",
               "focus.run.title": "전환 중…", "focus.run.body": "앞으로 가져오는 중:",
               "focus.ok.title": "전환됨", "focus.fail.title": "전환 실패",
               "focus.fail.notfound": "사이드바에서 그 프로젝트를 못 찾음. ~/.local/state/nonya/aliases.json에 별칭을 추가하세요.",
               "focus.fail.ambiguous": "비슷한 항목이 여러 개라 안전하게 고르지 못함.",
               "focus.fail.capture": "화면 기록 권한이 필요합니다 (설정 → 개인정보 보호 → 화면 기록).",
               "focus.fail.ax": "손쉬운 사용 권한이 필요합니다 (설정 → 개인정보 보호 → 손쉬운 사용).",
               "focus.fail.noproc": "대상 앱이 실행 중이 아닙니다.",
               "focus.fail.focus": "앱을 앞으로 가져오지 못했습니다.",
               "focus.fail.nohint": "매칭할 이름이 없습니다.", "focus.fail.generic": "대상을 확정하지 못했습니다.",
               "briefing.loading": "야간 브리핑 불러오는 중…", "briefing.title": "노냐? — 야간 브리핑"],
        "ja": ["stop": "停止", "watch.claude.app": "Claude を監視 (アプリ)", "watch.claude.cli": "Claude を監視 (CLI)",
               "watch.codex.app": "Codex を監視 (アプリ)", "watch.codex.cli": "Codex を監視 (CLI)", "watch.all": "全アプリを監視",
               "mode.overnight": "自律モード (夜間)", "briefing": "夜間ブリーフィング",
               "eyestyles": "目のスタイル… (プレビュー)", "perms": "権限設定を開く", "quit": "終了", "language": "言語"],
        "zh-Hans": ["stop": "停止", "watch.claude.app": "监视 Claude (应用)", "watch.claude.cli": "监视 Claude (CLI)",
               "watch.codex.app": "监视 Codex (应用)", "watch.codex.cli": "监视 Codex (CLI)", "watch.all": "监视全部应用",
               "mode.overnight": "自主模式 (通宵)", "briefing": "夜间简报",
               "eyestyles": "眼睛样式… (预览)", "perms": "打开权限设置", "quit": "退出", "language": "语言"],
        "zh-Hant": ["stop": "停止", "watch.claude.app": "監看 Claude (應用程式)", "watch.claude.cli": "監看 Claude (CLI)",
               "watch.codex.app": "監看 Codex (應用程式)", "watch.codex.cli": "監看 Codex (CLI)", "watch.all": "監看所有應用程式",
               "mode.overnight": "自主模式 (過夜)", "briefing": "夜間簡報",
               "eyestyles": "眼睛樣式… (預覽)", "perms": "開啟權限設定", "quit": "結束", "language": "語言"],
        "es": ["stop": "Detener", "watch.claude.app": "Vigilar Claude (app)", "watch.claude.cli": "Vigilar Claude (CLI)",
               "watch.codex.app": "Vigilar Codex (app)", "watch.codex.cli": "Vigilar Codex (CLI)", "watch.all": "Vigilar todas las apps",
               "mode.overnight": "Modo autónomo (nocturno)", "briefing": "Informe nocturno",
               "eyestyles": "Estilo de ojos… (vista previa)", "perms": "Abrir ajustes de permisos", "quit": "Salir", "language": "Idioma"],
        "fr": ["stop": "Arrêter", "watch.claude.app": "Surveiller Claude (app)", "watch.claude.cli": "Surveiller Claude (CLI)",
               "watch.codex.app": "Surveiller Codex (app)", "watch.codex.cli": "Surveiller Codex (CLI)", "watch.all": "Surveiller toutes les apps",
               "mode.overnight": "Mode autonome (nuit)", "briefing": "Briefing nocturne",
               "eyestyles": "Style des yeux… (aperçu)", "perms": "Ouvrir les réglages d'autorisation", "quit": "Quitter", "language": "Langue"],
        "de": ["stop": "Stopp", "watch.claude.app": "Claude überwachen (App)", "watch.claude.cli": "Claude überwachen (CLI)",
               "watch.codex.app": "Codex überwachen (App)", "watch.codex.cli": "Codex überwachen (CLI)", "watch.all": "Alle Apps überwachen",
               "mode.overnight": "Autonomer Modus (über Nacht)", "briefing": "Nacht-Briefing",
               "eyestyles": "Augen-Stil… (Vorschau)", "perms": "Berechtigungen öffnen", "quit": "Beenden", "language": "Sprache"],
        "pt-BR": ["stop": "Parar", "watch.claude.app": "Observar Claude (app)", "watch.claude.cli": "Observar Claude (CLI)",
               "watch.codex.app": "Observar Codex (app)", "watch.codex.cli": "Observar Codex (CLI)", "watch.all": "Observar todos os apps",
               "mode.overnight": "Modo autônomo (noturno)", "briefing": "Resumo noturno",
               "eyestyles": "Estilo dos olhos… (prévia)", "perms": "Abrir ajustes de permissão", "quit": "Sair", "language": "Idioma"],
    ]
}

// MARK: - metrics dashboard (native — reads <state>/ledger.jsonl directly, NO subprocess)
// The old metrics view shelled out to the frozen `nonya --metrics` binary (slow cold start) and
// dumped plain text into an NSAlert (unreadable). This reads the ledger in-process (instant) and
// draws a real KPI + bar-chart dashboard with Core Graphics, matching the eyes' native aesthetic.

struct NMetrics {
    var entries = 0, acted = 0, recovered = 0, shadow = 0, waitingInjections = 0
    var byClass: [(String, Int)] = [], byOutcome: [(String, Int)] = []
    var chainLinked = true
    var deliveryPct: Int? { acted > 0 ? Int((Double(recovered) / Double(acted) * 100).rounded()) : nil }
    var safe: Bool { waitingInjections == 0 }
}

// windowHours>0 limits the STATS to the last N hours (12/24/48); the ledger keeps full history
// (durable audit), and the hash CHAIN is always checked over ALL entries (integrity is whole-chain).
func loadMetrics(windowHours: Double = 0) -> NMetrics {
    var m = NMetrics()
    let url = stateURL().deletingLastPathComponent().appendingPathComponent("ledger.jsonl")
    guard let text = try? String(contentsOf: url, encoding: .utf8) else { return m }
    let cutoff = windowHours > 0 ? Date().timeIntervalSince1970 - windowHours * 3600 : 0
    var cls: [String: Int] = [:], out: [String: Int] = [:]
    var lastHash: String? = nil
    for line in text.split(separator: "\n") where !line.isEmpty {
        guard let d = line.data(using: .utf8),
              let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { continue }
        // chain integrity over the FULL ledger (before any window filter)
        if let prev = o["prev_hash"] as? String, let lh = lastHash, prev != lh { m.chainLinked = false }
        if let h = o["hash"] as? String { lastHash = h }
        if cutoff > 0 {
            let ts = (o["ts"] as? Double) ?? Double((o["ts"] as? Int) ?? 0)
            if ts < cutoff { continue }            // outside the window -> not counted in the stats
        }
        m.entries += 1
        let sc = (o["stall_class"] as? String) ?? "?"
        let oc = ((o["outcome"] as? String) ?? "?").lowercased()
        cls[sc, default: 0] += 1; out[oc, default: 0] += 1
        let injected = !(((o["injected_text"] as? String) ?? "").isEmpty)
        if injected { m.acted += 1 }
        if oc == "recovered" || oc == "resolved" { m.recovered += 1 }
        if oc == "shadow" { m.shadow += 1 }
        let scl = sc.lowercased()
        if injected && (scl.contains("waiting") || scl.contains("ask") || scl.contains("needs-you")) {
            m.waitingInjections += 1
        }
    }
    m.byClass = cls.sorted { $0.value > $1.value }.map { ($0.key, $0.value) }
    m.byOutcome = out.sorted { $0.value > $1.value }.map { ($0.key, $0.value) }
    return m
}

func nmColor(_ key: String) -> NSColor {
    switch key.lowercased() {
    case let k where k.contains("no-effect"):                    return NSColor(srgbRed: 1.0, green: 0.34, blue: 0.30, alpha: 1)
    case let k where k.contains("stuck"):                        return NSColor(srgbRed: 1.0, green: 0.30, blue: 0.27, alpha: 1)
    case let k where k.contains("waiting") || k.contains("ask"): return NSColor(srgbRed: 1.0, green: 0.82, blue: 0.30, alpha: 1)
    case let k where k.contains("looping"):                      return NSColor(srgbRed: 0.74, green: 0.45, blue: 1.0,  alpha: 1)
    case let k where k.contains("rate"):                         return NSColor(srgbRed: 0.97, green: 0.62, blue: 0.20, alpha: 1)
    case let k where k.contains("recover") || k.contains("resolved") || k.contains("keep") || k.contains("working"):
        return NSColor(srgbRed: 0.30, green: 0.82, blue: 0.52, alpha: 1)
    case let k where k.contains("inject") || k.contains("shadow"):return NSColor(srgbRed: 0.40, green: 0.62, blue: 1.0, alpha: 1)
    case let k where k.contains("alert") || k.contains("escal"): return NSColor(srgbRed: 0.95, green: 0.55, blue: 0.30, alpha: 1)
    case let k where k.contains("done"):                         return NSColor(srgbRed: 0.36, green: 0.84, blue: 0.80, alpha: 1)
    default:                                                     return NSColor(srgbRed: 0.55, green: 0.60, blue: 0.70, alpha: 1)
    }
}

func nmStWord(_ k: String) -> String { let t = L10n.t("st." + k); return t == "st." + k ? k : t }

// The intervention LOG: every nudge with WHEN, WHICH session, WHAT was sent, and the verified
// OUTCOME — read straight from the hash-chained ledger so the user can audit each one against
// reality. Newest first; the verify pass appends "recovered"/"no-effect" after each "injected".
func loadLogAttributed(limit: Int = 60, windowHours: Double = 0) -> NSAttributedString {
    let out = NSMutableAttributedString()
    let url = stateURL().deletingLastPathComponent().appendingPathComponent("ledger.jsonl")
    guard let text = try? String(contentsOf: url, encoding: .utf8) else {
        return NSAttributedString(string: L10n.t("m.logempty"),
                                  attributes: [.foregroundColor: NSColor.secondaryLabelColor,
                                               .font: NSFont.systemFont(ofSize: 12)])
    }
    let cutoff = windowHours > 0 ? Date().timeIntervalSince1970 - windowHours * 3600 : 0
    var rows: [[String: Any]] = []
    for line in text.split(separator: "\n") where !line.isEmpty {
        if let d = line.data(using: .utf8), let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] {
            if cutoff > 0 {
                let ts = (o["ts"] as? Double) ?? Double((o["ts"] as? Int) ?? 0)
                if ts < cutoff { continue }
            }
            rows.append(o)
        }
    }
    if rows.isEmpty {
        return NSAttributedString(string: L10n.t("m.logempty"),
                                  attributes: [.foregroundColor: NSColor.secondaryLabelColor, .font: NSFont.systemFont(ofSize: 12)])
    }
    // explicit premium colors (the dashboard is always dark; don't rely on appearance-dynamic colors,
    // which render invisible in the offscreen PNG and washed-out elsewhere).
    let cMut = NSColor(srgbRed: 0.52, green: 0.57, blue: 0.66, alpha: 1)
    let cTxt = NSColor(srgbRed: 0.90, green: 0.92, blue: 0.96, alpha: 1)
    let cDim = NSColor(srgbRed: 0.42, green: 0.46, blue: 0.55, alpha: 1)
    let para = NSMutableParagraphStyle(); para.lineSpacing = 5; para.paragraphSpacing = 3
    let df = DateFormatter(); df.dateFormat = "MM-dd HH:mm:ss"
    let mono = NSFont.monospacedSystemFont(ofSize: 11.5, weight: .regular)
    out.append(NSAttributedString(string: L10n.t("m.log").uppercased() + "\n\n", attributes: [
        .foregroundColor: cMut, .kern: 1.2,
        .font: NSFont.systemFont(ofSize: 10, weight: .semibold)]))
    for o in rows.reversed().prefix(limit) {
        let ts = (o["ts"] as? Double) ?? Double((o["ts"] as? Int) ?? 0)
        let when = ts > 0 ? df.string(from: Date(timeIntervalSince1970: ts)) : "—"
        let sess = (o["session"] as? String) ?? "?"
        let sc = (o["stall_class"] as? String) ?? "?"
        let oc = ((o["outcome"] as? String) ?? "?").lowercased()
        let injected = (o["injected_text"] as? String) ?? ""
        let line = NSMutableAttributedString()
        line.append(NSAttributedString(string: when + "  ", attributes: [.foregroundColor: cMut, .font: mono, .paragraphStyle: para]))
        line.append(NSAttributedString(string: sess, attributes: [.foregroundColor: cTxt, .font: NSFont.monospacedSystemFont(ofSize: 11.5, weight: .semibold), .paragraphStyle: para]))
        line.append(NSAttributedString(string: "  " + nmStWord(sc) + " → ", attributes: [.foregroundColor: cMut, .font: mono, .paragraphStyle: para]))
        line.append(NSAttributedString(string: nmStWord(oc), attributes: [.foregroundColor: nmColor(oc), .font: NSFont.monospacedSystemFont(ofSize: 11.5, weight: .bold), .paragraphStyle: para]))
        if !injected.isEmpty {
            let snip = injected.count > 32 ? String(injected.prefix(32)) + "…" : injected
            line.append(NSAttributedString(string: "  «" + snip + "»", attributes: [.foregroundColor: cDim, .font: mono, .paragraphStyle: para]))
        }
        line.append(NSAttributedString(string: "\n", attributes: [.font: mono, .paragraphStyle: para]))
        out.append(line)
    }
    if out.length == 0 {
        return NSAttributedString(string: L10n.t("m.logempty"),
                                  attributes: [.foregroundColor: NSColor.secondaryLabelColor, .font: NSFont.systemFont(ofSize: 12)])
    }
    return out
}

final class MetricsView: NSView {
    var m = NMetrics() { didSet { needsDisplay = true } }

    func barColor(_ key: String) -> NSColor { nmColor(key) }

    private func text(_ s: String, _ x: CGFloat, _ y: CGFloat, _ size: CGFloat,
                      _ color: NSColor, weight: NSFont.Weight = .regular, align: NSTextAlignment = .left,
                      width: CGFloat = 0) {
        let p = NSMutableParagraphStyle(); p.alignment = align; p.lineBreakMode = .byTruncatingTail
        let a: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: size, weight: weight),
                                                .foregroundColor: color, .paragraphStyle: p]
        let w = width > 0 ? width : bounds.width
        (s as NSString).draw(in: NSRect(x: x, y: y, width: w, height: size + 8), withAttributes: a)
    }

    // letter-spaced uppercase mini-label (premium section caption)
    private func cap(_ s: String, _ x: CGFloat, _ y: CGFloat, _ color: NSColor, _ w: CGFloat = 0) {
        let p = NSMutableParagraphStyle(); p.lineBreakMode = .byTruncatingTail
        let a: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 10, weight: .semibold),
            .foregroundColor: color, .kern: 1.2, .paragraphStyle: p]
        (s.uppercased() as NSString).draw(in: NSRect(x: x, y: y, width: w > 0 ? w : bounds.width, height: 16), withAttributes: a)
    }

    override func draw(_ dirty: NSRect) {
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }
        let b = bounds, pad: CGFloat = 24
        // premium dark palette (self-contained, not appearance-dependent)
        let bg0 = NSColor(srgbRed: 0.075, green: 0.082, blue: 0.106, alpha: 1)
        let bg1 = NSColor(srgbRed: 0.043, green: 0.047, blue: 0.063, alpha: 1)
        let cardFill = NSColor(srgbRed: 0.110, green: 0.122, blue: 0.153, alpha: 1)
        let cardEdge = NSColor(srgbRed: 1, green: 1, blue: 1, alpha: 0.07)
        let txt = NSColor(srgbRed: 0.92, green: 0.94, blue: 0.97, alpha: 1)
        let mut = NSColor(srgbRed: 0.55, green: 0.60, blue: 0.70, alpha: 1)
        let track = NSColor(srgbRed: 1, green: 1, blue: 1, alpha: 0.06)
        let green = NSColor(srgbRed: 0.30, green: 0.86, blue: 0.56, alpha: 1)
        let red = NSColor(srgbRed: 1.0, green: 0.36, blue: 0.34, alpha: 1)
        func rrect(_ r: NSRect, _ rad: CGFloat, _ c: NSColor) { c.setFill(); NSBezierPath(roundedRect: r, xRadius: rad, yRadius: rad).fill() }
        func gradbar(_ r: NSRect, _ rad: CGFloat, _ c: NSColor) {
            NSGraphicsContext.saveGraphicsState()
            NSBezierPath(roundedRect: r, xRadius: rad, yRadius: rad).addClip()
            let top = c.blended(withFraction: 0.22, of: .white) ?? c
            let bot = c.blended(withFraction: 0.16, of: .black) ?? c
            NSGradient(colors: [top, bot])?.draw(in: r, angle: -90)
            NSGraphicsContext.restoreGraphicsState()
        }
        // background: vertical gradient (premium depth)
        NSGradient(colors: [bg0, bg1])?.draw(in: b, angle: -90)
        if m.entries == 0 {
            text(L10n.t("m.none"), pad, b.midY, 14, mut, width: b.width - pad * 2)
            return
        }
        var y = b.height - pad
        let alertOnly = m.byOutcome.first(where: { $0.0 == "alert-only" })?.1 ?? 0
        // context caption
        cap("\(L10n.t("m.interventions")) \(m.entries)  ·  \(L10n.t("st.alert-only")) \(alertOnly)", pad, y - 14, mut, b.width - pad * 2)
        y -= 30
        // ---- KPI cards: keys sent / confirmed recovered / safety invariant ----
        let cardW = (b.width - pad * 2 - 24) / 3, cardH: CGFloat = 84
        let kpiY = y - cardH
        let deliv = m.acted > 0 ? "\(Int(round(Double(m.recovered) / Double(m.acted) * 100)))%" : "—"
        let cards: [(String, String, String, NSColor)] = [
            (L10n.t("m.keys"), "\(m.acted)", L10n.t("m.recovered") + " \(m.recovered) · " + deliv, m.acted > 0 ? green : mut),
            (L10n.t("m.recovered"), "\(m.recovered)", L10n.t("m.delivery") + " " + deliv, m.recovered > 0 ? green : mut),
            (L10n.t("m.safety"), m.safe ? L10n.t("m.ok") : L10n.t("m.violated"), L10n.t("m.shadow") + " \(m.shadow)", m.safe ? green : red),
        ]
        for (i, c) in cards.enumerated() {
            let x = pad + CGFloat(i) * (cardW + 12)
            let r = NSRect(x: x, y: kpiY, width: cardW, height: cardH)
            rrect(r, 16, cardFill)
            cardEdge.setStroke()
            let bz = NSBezierPath(roundedRect: r.insetBy(dx: 0.5, dy: 0.5), xRadius: 16, yRadius: 16); bz.lineWidth = 1; bz.stroke()
            // soft accent glow bar at top
            NSGraphicsContext.saveGraphicsState()
            c.3.withAlphaComponent(0.85).setFill()
            NSBezierPath(roundedRect: NSRect(x: x + 14, y: kpiY + cardH - 5, width: cardW - 28, height: 3), xRadius: 1.5, yRadius: 1.5).fill()
            NSGraphicsContext.restoreGraphicsState()
            cap(c.0, x + 16, kpiY + cardH - 26, mut, cardW - 24)
            text(c.1, x + 15, kpiY + 22, 30, c.3, weight: .bold)
            text(c.2, x + 16, kpiY + 8, 10, mut, weight: .medium, width: cardW - 24)
        }
        y = kpiY - 26
        func stWord(_ k: String) -> String { let t = L10n.t("st." + k); return t == "st." + k ? k : t }
        // ---- bar-chart section (gradient bars on subtle tracks) ----
        func section(_ title: String, _ rows: [(String, Int)]) {
            cap(title, pad, y - 12, mut); y -= 28
            let maxV = max(1, rows.map { $0.1 }.max() ?? 1)
            let labelW: CGFloat = 104, barX = pad + labelW + 10
            let barMaxW = b.width - barX - pad - 46
            for (k, v) in rows.prefix(6) {
                let rowH: CGFloat = 26, barH: CGFloat = 15
                let trackY = y - rowH + (rowH - barH) / 2 + 1
                text(stWord(k), pad - 2, y - rowH + 5, 12.5, txt, weight: .medium, align: .right, width: labelW)
                rrect(NSRect(x: barX, y: trackY, width: barMaxW, height: barH), barH / 2, track)
                let w = max(barH, barMaxW * CGFloat(v) / CGFloat(maxV))
                gradbar(NSRect(x: barX, y: trackY, width: w, height: barH), barH / 2, nmColor(k))
                text("\(v)", barX + barMaxW + 8, y - rowH + 5, 12.5, txt, weight: .semibold, width: 42)
                y -= rowH
            }
            y -= 16
        }
        section(L10n.t("m.byclass"), m.byClass)
        section(L10n.t("m.byoutcome"), m.byOutcome)
        // ---- footer: chain integrity pill ----
        let chain = m.chainLinked ? L10n.t("m.linked") : L10n.t("m.broken")
        let dot = m.chainLinked ? green : red
        dot.setFill(); NSBezierPath(ovalIn: NSRect(x: pad, y: pad - 4, width: 7, height: 7)).fill()
        text("\(L10n.t("m.chain")): \(chain)", pad + 13, pad - 9, 11, mut, weight: .medium, width: b.width - pad * 2)
        _ = ctx
    }
}

// MARK: - app

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var eyesView: EyesView!
    private var stateTimer: Timer?
    private var hoverTimer: Timer?
    private var procs: [Process] = []
    // Intervention mode = ONE source of truth: the `nonya.mode` setting (also in config.json, which
    // the core hot-applies every poll). The menu radio and the Settings popup both read/write it,
    // so they can never disagree. Default = on-error (only act on errors/stalls).
    private var autoMode: Bool { (UserDefaults.standard.string(forKey: "nonya.mode") ?? "on-error") == "auto" }
    // SHADOW is orthogonal to mode: decide per the mode but send ZERO keys (record would-haves).
    private var shadowMode: Bool { UserDefaults.standard.bool(forKey: "nonya.shadow") }
    private var stylePicker: NSWindow?
    private var settingsWin: NSWindow?
    private var previewWin: NSWindow?
    private var previewTimer: Timer?
    private var notifTimer: Timer?
    private var updateTimer: Timer?
    private var notifOffset: UInt64 = 0          // bytes consumed from notifications.jsonl
    private var lastStatus = "watching"

    func applicationDidFinishLaunching(_ n: Notification) {
        if let i = CommandLine.arguments.firstIndex(of: "--render-states"), i + 1 < CommandLine.arguments.count {
            renderStates(dir: CommandLine.arguments[i + 1]); NSApp.terminate(nil); return
        }
        if let i = CommandLine.arguments.firstIndex(of: "--render-metrics"), i + 1 < CommandLine.arguments.count {
            renderMetrics(path: CommandLine.arguments[i + 1]); NSApp.terminate(nil); return
        }
        if let i = CommandLine.arguments.firstIndex(of: "--inject-test-app"), i + 1 < CommandLine.arguments.count {
            // headless verification of the EXACT menu-button path (in-process injectScold under
            // NonyaPet.app's own Accessibility grant). Result -> file (open-launched app has no stdout).
            let proc = CommandLine.arguments[i + 1]
            Task {
                let res = await injectScold(into: proc, "테스트니 무시하세요", send: true)
                try? res.write(toFile: "/tmp/nonya-injecttest-result.txt", atomically: true, encoding: .utf8)
                DispatchQueue.main.async { NSApp.terminate(nil) }
            }
            return
        }
        // GUI inject for the Python scanner: raise <App> + type+send <text> via CGEvent (Accessibility —
        // no Apple Events/Automation). The scanner calls this (NONYA_AX_HELPER) instead of its own
        // osascript. Accessibility alone is enough to deliver (blind submit fallback); Screen Recording,
        // when granted, additionally lets injectScold capture-VERIFY the text landed and was sent.
        if let i = CommandLine.arguments.firstIndex(of: "--inject-app"), i + 1 < CommandLine.arguments.count {
            let a = CommandLine.arguments
            let txt = i + 2 < a.count ? a[i + 2] : "계속 진행"
            Task {
                let res = await injectScold(into: a[i + 1], txt, send: true)
                print(res)
                exit(res.hasPrefix("OK") ? 0 : 2)
            }
            return
        }
        if let i = CommandLine.arguments.firstIndex(of: "--render-icon"), i + 1 < CommandLine.arguments.count {
            renderIcon(CommandLine.arguments[i + 1]); NSApp.terminate(nil); return
        }
        if let i = CommandLine.arguments.firstIndex(of: "--ax-dump"), i + 1 < CommandLine.arguments.count {
            let a = CommandLine.arguments
            axDump(appName: a[i + 1], needle: i + 2 < a.count ? a[i + 2] : ""); exit(0)
        }
        if let i = CommandLine.arguments.firstIndex(of: "--ax-inject-focused"), i + 2 < CommandLine.arguments.count {
            let a = CommandLine.arguments
            exit(axInjectFocused(appName: a[i + 1], text: a[i + 2], sendKey: i + 3 < a.count ? a[i + 3] : "return"))
        }
        if let i = CommandLine.arguments.firstIndex(of: "--ax-type"), i + 3 < CommandLine.arguments.count {
            let a = CommandLine.arguments
            exit(axTypeMatch(appName: a[i + 1], match: a[i + 2], text: a[i + 3]))
        }
        // headless AX split-inject helper (the core calls this to recover a background terminal split):
        //   --ax-inject <appName> <matchText> <nudgeText> [send-key]
        if let i = CommandLine.arguments.firstIndex(of: "--ax-inject"), i + 3 < CommandLine.arguments.count {
            let a = CommandLine.arguments
            let rc = axInjectSplit(appName: a[i + 1], match: a[i + 2], text: a[i + 3],
                                   sendKey: i + 4 < a.count ? a[i + 4] : "return")
            exit(rc)
        }
        // M3: Vision-OCR desktop targeting (in-process — capture needs this GUI/WindowServer context).
        //   --resolve-focus  <App> <hint>                 : "전환" — bring the matched session to front (no typing)
        //   --resolve-inject <App> <hint> <text> [sendkey]: select the exact session, type, verify, submit
        if let i = CommandLine.arguments.firstIndex(of: "--resolve-focus"), i + 2 < CommandLine.arguments.count {
            let a = CommandLine.arguments
            Task { let r = await resolveFocus(appName: a[i + 1], hint: a[i + 2]); print(r); exit(r.hasPrefix("OK") ? 0 : 2) }
            return                                          // let the Task run on the runloop
        }
        if let i = CommandLine.arguments.firstIndex(of: "--resolve-inject"), i + 3 < CommandLine.arguments.count {
            let a = CommandLine.arguments
            let sk = i + 4 < a.count ? a[i + 4] : "return"
            Task { let r = await resolveInject(appName: a[i + 1], hint: a[i + 2], text: a[i + 3], sendKey: sk); print(r); exit(r.hasPrefix("OK") ? 0 : 2) }
            return
        }
        // diagnostic: capture+OCR <App> and dump every text run + score vs <hint>. NO raise, NO click.
        if let i = CommandLine.arguments.firstIndex(of: "--resolve-debug"), i + 2 < CommandLine.arguments.count {
            let a = CommandLine.arguments
            Task { print(await resolveDebug(appName: a[i + 1], hint: a[i + 2])); exit(0) }
            return
        }
        // visual state sensor (capture+OCR only): prints error / working / idle for <App>.
        if let i = CommandLine.arguments.firstIndex(of: "--resolve-state"), i + 1 < CommandLine.arguments.count {
            let a = CommandLine.arguments
            Task { print(await resolveState(appName: a[i + 1])); exit(0) }
            return
        }
        // codex 전환: deep-link to a Codex Desktop thread by id (headless-testable mirror of the menu).
        if let i = CommandLine.arguments.firstIndex(of: "--codex-focus"), i + 1 < CommandLine.arguments.count {
            let id = CommandLine.arguments[i + 1]
            if let url = URL(string: "codex://threads/\(id)") { NSWorkspace.shared.open(url); print("opened \(url.absoluteString)") }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { exit(0) }
            return
        }
        // CLI 전환: focus the terminal split running a session, matched by an on-screen content needle.
        if let i = CommandLine.arguments.firstIndex(of: "--focus-terminal"), i + 1 < CommandLine.arguments.count {
            print(_focusTerminalSplit(CommandLine.arguments[i + 1]))
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { exit(0) }
            return
        }
        // single instance: if another 노냐 is already running, quit — no duplicate menu-bar eyes
        // (open -na / double-launch would otherwise stack icons).
        let mine = NSRunningApplication.current.processIdentifier
        let bid = Bundle.main.bundleIdentifier ?? "com.nonya.pet"
        if NSRunningApplication.runningApplications(withBundleIdentifier: bid)
            .contains(where: { $0.processIdentifier != mine }) {
            NSApp.terminate(nil); return
        }
        statusItem = NSStatusBar.system.statusItem(withLength: 48)
        if let btn = statusItem.button {
            btn.image = nil; btn.title = ""
            eyesView = EyesView(frame: btn.bounds)
            eyesView.mood = "idle"                         // not watching yet -> eyes closed/asleep
            eyesView.autoresizingMask = [.width, .height]
            btn.addSubview(eyesView)                       // live eyes IN the menu bar — OS-managed slot, never collides
        }
        rebuildMenu(running: false)
        startHover()
        setupNotifications()
        scheduleAutoUpdate()              // GitHub auto-update (ON by default; gated by the Settings checkbox)
        // AUTO-RESUME: if monitoring was ON when we last quit (or crashed / were replaced by an update),
        // restart the SAME watch target — otherwise a restart silently leaves nonya asleep while the user
        // believes it's watching (the "감시 안 됨" trap). An explicit Stop clears the flag so we stay off.
        if UserDefaults.standard.bool(forKey: "nonya.watching"),
           let saved = UserDefaults.standard.array(forKey: "nonya.watchTarget") as? [[String]], !saved.isEmpty {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in self?.start(saved) }
        }
        if CommandLine.arguments.contains("--mirror") { showWatch() }   // mirror state.json into the eyes (no core; for live verification)
        if CommandLine.arguments.contains("--styles") { DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in self?.openStylePicker() } }
        if CommandLine.arguments.contains("--briefing") { DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in self?.showBriefing() } }
        if CommandLine.arguments.contains("--settings") { DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in self?.openSettings() } }
    }

    // headless: render each supervisor state to a PNG strip so the eye states can be reviewed
    // Premium 1024px app icon in nonya's own visual language: a glowing neon "watching"
    // eye-pair on a deep cyber squircle. Crisp vector/CG (the right medium for an app icon,
    // unlike a diffusion render). Output -> iconset -> AppIcon.icns by packaging/make-icon.sh.
    func renderIcon(_ path: String) {
        let S: CGFloat = 1024
        let img = NSImage(size: NSSize(width: S, height: S))
        img.lockFocus()
        guard let ctx = NSGraphicsContext.current?.cgContext else { img.unlockFocus(); return }
        func col(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> NSColor {
            NSColor(srgbRed: r, green: g, blue: b, alpha: a)
        }
        func grad(_ cols: [NSColor], _ locs: [CGFloat]) -> CGGradient {
            CGGradient(colorsSpace: CGColorSpace(name: CGColorSpace.sRGB)!,
                       colors: cols.map { $0.cgColor } as CFArray, locations: locs)!
        }
        // floating squircle "card" (modern macOS icon grid: ~80% with a soft shadow)
        let m: CGFloat = 96, side = S - m * 2
        let card = CGRect(x: m, y: m, width: side, height: side)
        let squircle = CGPath(roundedRect: card, cornerWidth: side * 0.225, cornerHeight: side * 0.225, transform: nil)
        ctx.saveGState()
        ctx.setShadow(offset: CGSize(width: 0, height: -18), blur: 48, color: col(0, 0, 0, 0.45).cgColor)
        ctx.addPath(squircle); col(0.04, 0.07, 0.11).setFill(); ctx.fillPath()
        ctx.restoreGState()
        ctx.saveGState(); ctx.addPath(squircle); ctx.clip()
        // base diagonal gradient (deep teal-navy)
        ctx.drawLinearGradient(grad([col(0.07, 0.16, 0.24), col(0.03, 0.06, 0.10)], [0, 1]),
                               start: CGPoint(x: card.minX, y: card.maxY), end: CGPoint(x: card.maxX, y: card.minY), options: [])
        // center teal glow behind the eyes
        ctx.drawRadialGradient(grad([col(0.20, 0.85, 0.78, 0.40), col(0.20, 0.85, 0.78, 0)], [0, 1]),
                               startCenter: CGPoint(x: S/2, y: S/2 + 30), startRadius: 0,
                               endCenter: CGPoint(x: S/2, y: S/2 + 30), endRadius: side * 0.52, options: [])
        // the eyes — glowing neon rings, alert/watching
        let iris = col(0.44, 0.94, 0.86)
        let ry: CGFloat = 168, rx = ry * 1.06, cy = S/2 + 24, gap = rx * 2.5
        for cx in [S/2 - gap/2, S/2 + gap/2] {
            let lens = CGRect(x: cx-rx, y: cy-ry, width: rx*2, height: ry*2)
            ctx.saveGState(); ctx.addEllipse(in: lens); ctx.clip()
            ctx.drawRadialGradient(grad([col(0.10, 0.13, 0.16), col(0.02, 0.03, 0.04)], [0, 1]),
                                   startCenter: CGPoint(x: cx, y: cy+ry*0.2), startRadius: 0,
                                   endCenter: CGPoint(x: cx, y: cy+ry*0.2), endRadius: rx*1.2, options: [.drawsAfterEndLocation])
            ctx.restoreGState()
            let ix = cx + rx*0.16, iy = cy + ry*0.10, ir = ry*0.72   // slight up-right gaze
            ctx.saveGState()
            ctx.setShadow(offset: .zero, blur: ir*0.9, color: iris.cgColor)
            ctx.setStrokeColor(iris.cgColor); ctx.setLineWidth(ir*0.30)
            ctx.strokeEllipse(in: CGRect(x: ix-ir, y: iy-ir, width: ir*2, height: ir*2))
            ctx.restoreGState()
            ctx.saveGState(); ctx.addEllipse(in: CGRect(x: ix-ir*0.55, y: iy-ir*0.55, width: ir*1.1, height: ir*1.1)); ctx.clip()
            ctx.drawRadialGradient(grad([iris.blended(withFraction: 0.7, of: .white)!, iris], [0, 1]),
                                   startCenter: CGPoint(x: ix, y: iy), startRadius: 0,
                                   endCenter: CGPoint(x: ix, y: iy), endRadius: ir*0.55, options: [.drawsAfterEndLocation])
            ctx.restoreGState()
            col(0.02, 0.03, 0.04).setFill(); ctx.fillEllipse(in: CGRect(x: ix-ir*0.24, y: iy-ir*0.24, width: ir*0.48, height: ir*0.48))
            col(1, 1, 1, 0.95).setFill(); ctx.fillEllipse(in: CGRect(x: ix-ir*0.34, y: iy+ir*0.16, width: ir*0.22, height: ir*0.22))
        }
        // top glass highlight for depth
        ctx.drawLinearGradient(grad([col(1, 1, 1, 0.10), col(1, 1, 1, 0)], [0, 1]),
                               start: CGPoint(x: S/2, y: card.maxY), end: CGPoint(x: S/2, y: card.midY + 60), options: [])
        ctx.restoreGState()
        // crisp inner rim
        ctx.addPath(squircle); ctx.setStrokeColor(col(1, 1, 1, 0.08).cgColor); ctx.setLineWidth(2); ctx.strokePath()
        img.unlockFocus()
        if let tiff = img.tiffRepresentation, let bm = NSBitmapImageRep(data: tiff),
           let png = bm.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: path))
        }
    }

    func renderStates(dir: String) {
        let moods = ["idle", "watching", "working", "waiting", "looping", "stuck", "scolding", "rate-limited", "stopped", "done"]
        let w: CGFloat = 240, h: CGFloat = 64
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        for mood in moods {
            let v = EyesView(frame: NSRect(x: 0, y: 0, width: w, height: h))
            v.mood = mood; v.poseForRender()
            let img = NSImage(size: NSSize(width: w, height: h))
            img.lockFocus()
            NSColor(srgbRed: 0.10, green: 0.10, blue: 0.12, alpha: 1).setFill()   // menu-bar-like dark
            NSRect(x: 0, y: 0, width: w, height: h).fill()
            if let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) {
                v.cacheDisplay(in: v.bounds, to: rep)
                rep.draw(in: NSRect(x: 0, y: 0, width: w, height: h))
            }
            img.unlockFocus()
            if let tiff = img.tiffRepresentation, let bm = NSBitmapImageRep(data: tiff),
               let png = bm.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: dir + "/eye-" + mood + ".png"))
            }
        }
    }

    // Offscreen-render the full dashboard (charts + log) from the REAL ledger to a PNG (headless verify).
    func renderMetrics(path: String) {
        let w: CGFloat = 600, h: CGFloat = 808, chartsH: CGFloat = 548
        let v = MetricsView(frame: NSRect(x: 0, y: h - chartsH, width: w, height: chartsH)); v.m = loadMetrics()
        let img = NSImage(size: NSSize(width: w, height: h))
        img.lockFocus()
        NSColor(srgbRed: 0.043, green: 0.047, blue: 0.063, alpha: 1).setFill()   // deep premium bg (matches window)
        NSRect(x: 0, y: 0, width: w, height: h).fill()
        if let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) {
            v.cacheDisplay(in: v.bounds, to: rep); rep.draw(in: NSRect(x: 0, y: h - chartsH, width: w, height: chartsH))
        }
        loadLogAttributed(limit: 18).draw(in: NSRect(x: 18, y: 14, width: w - 36, height: h - chartsH - 24))
        img.unlockFocus()
        if let tiff = img.tiffRepresentation, let bm = NSBitmapImageRep(data: tiff),
           let png = bm.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: path))
        }
    }

    // a non-clickable section header so the two ORTHOGONAL axes read clearly:
    // WHAT to watch (the actions) vs HOW to intervene (the mode). They are independent —
    // picking a watch target does NOT change the mode, and the mode applies to every watch.
    private func header(_ title: String) -> NSMenuItem {
        let it = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        it.isEnabled = false
        it.attributedTitle = NSAttributedString(string: title, attributes: [
            .font: NSFont.systemFont(ofSize: NSFont.smallSystemFontSize, weight: .semibold),
            .foregroundColor: NSColor.secondaryLabelColor])
        return it
    }

    // A coloured dot + word for a session status, so the menu list reads at a glance.
    private func statusDot(_ st: String) -> String {
        switch st {
        case "working", "watching": return "🟢"
        case "waiting":             return "🟡"
        case "stuck":               return "🔴"
        case "looping":             return "🟣"
        case "rate-limited":        return "🟠"
        case "done":                return "✅"
        case "stopped":             return "⚪️"
        case "idle":                return "💤"
        default:                    return "•"
        }
    }
    // Live watched sessions (sessions/*.json fresh < 30min), deduped by label, most-urgent first.
    // Quiet between-turn sessions are not actionable, so hide "idle" rows from the menu.
    private func liveSessions() -> [(label: String, status: String, idle: Int, reach: String, engine: String, cwd: String, title: String, sid: String, snippet: String)] {
        let dir = stateURL().deletingLastPathComponent().appendingPathComponent("sessions")
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey]) else { return [] }
        var seen = Set<String>(); var rows: [(Int, String, String, Int, String, String, String, String, String, String)] = []
        for f in files where f.pathExtension == "json" {
            if let m = (try? f.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate,
               Date().timeIntervalSince(m) > 1800 { try? fm.removeItem(at: f); continue }
            guard let d = try? Data(contentsOf: f),
                  let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
                  let st = o["status"] as? String, st != "preview" else { continue }
            if st == "idle" { continue }
            // a single-session run writes <pid>.json; if that process is DEAD its file is a phantom
            // (e.g. a `--target cli` run that ended/crashed). Drop + delete it — don't show a ghost.
            if let p = o["pid"] as? Int, p > 0, kill(pid_t(p), 0) != 0, errno == ESRCH {
                try? fm.removeItem(at: f); continue
            }
            let label = (o["session"] as? String) ?? (o["target"] as? String) ?? "?"
            if seen.contains(label) { continue }
            seen.insert(label)
            rows.append((_moodRank[st] ?? 0, label, st, (o["idle"] as? Int) ?? 0, (o["reach"] as? String) ?? "alert",
                         (o["engine"] as? String) ?? "", (o["cwd"] as? String) ?? "", (o["title"] as? String) ?? "",
                         (o["session_id"] as? String) ?? "", (o["snippet"] as? String) ?? ""))
        }
        rows.sort { $0.0 != $1.0 ? $0.0 > $1.0 : $0.1 < $1.1 }
        return rows.map { (label: $0.1, status: $0.2, idle: $0.3, reach: $0.4, engine: $0.5, cwd: $0.6, title: $0.7, sid: $0.8, snippet: $0.9) }
    }

    func rebuildMenu(running: Bool) {
        let m = NSMenu(); m.delegate = self          // delegate -> repopulate on every open (live statuses)
        populate(m, running: running)
        statusItem.menu = m
    }
    // NSMenuDelegate: refresh the items each time the menu opens so the watched-session
    // list and their statuses are always current (the eyes update continuously; so should this).
    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        populate(menu, running: !procs.isEmpty)
    }
    private func populate(_ m: NSMenu, running: Bool) {
        if running {
            m.addItem(withTitle: L10n.t("stop"), action: #selector(stopAll), keyEquivalent: "")
            // WHAT nonya is currently watching, with live status — so the menu isn't just "Stop".
            m.addItem(.separator())
            m.addItem(header(L10n.t("menu.hdr.sessions")))
            let sessions = liveSessions()
            if sessions.isEmpty {
                let none = NSMenuItem(title: L10n.t("sess.none"), action: nil, keyEquivalent: "")
                none.isEnabled = false; m.addItem(none)
            } else {
                for s in sessions.prefix(12) {
                    // READABLE name, not the "engine:idtail" code: desktop conversation title (Claude),
                    // else the project folder name (Codex has no title store), else the raw label. Keep
                    // a short id suffix so two same-named sessions stay distinguishable.
                    var nm = s.title.isEmpty ? (s.cwd as NSString).lastPathComponent : s.title
                    if nm.isEmpty { nm = s.label.components(separatedBy: ":").first ?? s.label }
                    if nm.count > 32 { nm = String(nm.prefix(31)) + "…" }
                    let tail = s.label.components(separatedBy: ":").last ?? ""
                    let name = tail.isEmpty ? nm : "\(nm) ·\(tail)"
                    let title = "\(statusDot(s.status))  \(name) — \(L10n.t("st." + s.status))"
                    // CLICK = "전환": jump to this exact session (OCR-target its row in the app and
                    // focus it). Doubles as a live test of the targeting. ENABLED + PLAIN title so
                    // AppKit renders it full-strength and inverts to white on hover.
                    let it = NSMenuItem(title: title, action: #selector(focusSession(_:)), keyEquivalent: "")
                    it.target = self
                    it.representedObject = ["engine": s.engine, "cwd": s.cwd, "label": s.label, "title": s.title, "sid": s.sid, "snippet": s.snippet]
                    m.addItem(it)
                }
                if sessions.count > 12 {
                    let more = NSMenuItem(title: "   +\(sessions.count - 12)…", action: #selector(sessionRowNoop), keyEquivalent: "")
                    m.addItem(more)
                }
                // Honest "why isn't this deterministic?" hint: non-tmux sessions do not have a
                // pane-id target. GUI recovery may still act after OCR/idle gates; raw terminals
                // stay alert-only. Point users at Start-in-tmux for the reliable path.
                let alert = sessions.filter { $0.reach != "tmux" }.count
                if alert > 0 {
                    let tip = NSMenuItem(title: "💡 \(alert)\(L10n.t("sess.alertonly.tip"))", action: nil, keyEquivalent: "")
                    tip.isEnabled = false
                    tip.attributedTitle = NSAttributedString(string: tip.title, attributes: [
                        .font: NSFont.systemFont(ofSize: NSFont.smallSystemFontSize),
                        .foregroundColor: NSColor.secondaryLabelColor])
                    m.addItem(tip)
                }
            }
        } else {
            // ONE action: watch every installed/running agent session (Claude + Codex, app + CLI).
            // nonya detects what's there from the transcripts — no per-engine choice to make.
            let startItem = NSMenuItem(title: L10n.t("watch.start"), action: #selector(watchAllApps), keyEquivalent: "")
            m.addItem(startItem)
        }
        // INTERVENTION mode — shown in BOTH states (it's a sticky setting, not a one-shot action).
        // Two radio-style items make it obvious this is a single HOW choice, separate from WHAT.
        // Changing it while watching restarts the active watch with the new mode (takes effect now).
        m.addItem(.separator())
        m.addItem(header(L10n.t("menu.hdr.mode")))
        let onErr = NSMenuItem(title: L10n.t("mode.onerror"), action: #selector(modeOnError), keyEquivalent: "")
        onErr.state = autoMode ? .off : .on; m.addItem(onErr)
        let auto = NSMenuItem(title: L10n.t("mode.auto.full"), action: #selector(modeAuto), keyEquivalent: "")
        auto.state = autoMode ? .on : .off; m.addItem(auto)
        // SHADOW is orthogonal: with it on, nonya decides per the mode above but sends ZERO keys
        // (records would-haves). Run a while, then "Intervention metrics" to vet before trusting auto.
        let shadow = NSMenuItem(title: L10n.t("mode.shadow"), action: #selector(toggleShadow), keyEquivalent: "")
        shadow.state = shadowMode ? .on : .off; m.addItem(shadow)
        m.addItem(.separator())
        // single item -> opens the dashboard window; the time range (12/24/48h/all) is chosen INSIDE
        // the window via a segmented control (no submenu).
        m.addItem(withTitle: L10n.t("metrics"), action: #selector(showMetrics), keyEquivalent: "")
        m.addItem(withTitle: L10n.t("eyestyles"), action: #selector(openStylePicker), keyEquivalent: "")
        // language picker
        let langItem = NSMenuItem(title: L10n.t("language"), action: nil, keyEquivalent: "")
        let langMenu = NSMenu()
        for (code, name) in L10n.choices {
            let it = NSMenuItem(title: name, action: #selector(pickLanguage(_:)), keyEquivalent: "")
            it.representedObject = code; it.state = (code == L10n.pref) ? .on : .off; it.target = self
            langMenu.addItem(it)
        }
        langItem.submenu = langMenu; m.addItem(langItem)
        m.addItem(.separator())
        m.addItem(withTitle: L10n.t("settings"), action: #selector(openSettings), keyEquivalent: ",")
        m.addItem(withTitle: L10n.t("update.check"), action: #selector(checkForUpdateManual), keyEquivalent: "")
        m.addItem(withTitle: L10n.t("perms"), action: #selector(openAX), keyEquivalent: "")
        m.addItem(withTitle: L10n.t("quit"), action: #selector(quit), keyEquivalent: "q")
        m.items.forEach { if $0.action != nil { $0.target = self } }
    }

    // The status-bar eyes track the cursor.
    func startHover() {
        hoverTimer?.invalidate()
        hoverTimer = Timer.scheduledTimer(withTimeInterval: 0.06, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            let m = NSEvent.mouseLocation
            if let sw = self.statusItem.button?.window, let ev = self.eyesView, ev.tracksCursor, let scr = NSScreen.main {
                let r = sw.convertToScreen(ev.convert(ev.bounds, to: nil))
                let dx = max(-1.0, min(1.0, (m.x - r.midX) / (scr.frame.width * 0.5)))
                let dy = max(-1.0, min(1.0, (m.y - r.midY) / (scr.frame.height * 0.55)))
                ev.setGaze(dx, dy)
            }
        }
    }

    // poll nonya's state.json and mirror the supervisor's state in the menu-bar eyes (the core injects).
    func showWatch() {
        startHover()
        stateTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            if !self.procs.isEmpty && self.procs.allSatisfy({ !$0.isRunning }) {   // core exited -> back to dormant
                self.procs.removeAll(); self.currentWatch = nil
                self.eyesView?.mood = "idle"; self.lastStatus = "idle"
                self.rebuildMenu(running: false); return
            }
            let raw = readStateRaw()
            if (raw["status"] as? String) == "preview" {         // core is counting down before an injection
                let txt = (raw["preview_text"] as? String) ?? ""
                let dl = (raw["deadline"] as? Int) ?? (Int(Date().timeIntervalSince1970) + ((raw["preview_secs"] as? Int) ?? 5))
                self.showPreview(txt, deadline: dl)
                self.eyesView?.mood = "waiting"; self.lastStatus = "preview"; return
            }
            self.closePreview()
            let st = urgentStatus() ?? "watching"                // most urgent across all watched sessions
            self.eyesView?.mood = st
            self.lastStatus = st
        }
    }

    // The watch TARGET (what to watch), WITHOUT the mode flags — modeArgs() is appended at spawn,
    // so flipping the intervention mode can restart the same target with the new mode.
    private var currentWatch: [[String]]?

    func start(_ baseArgSets: [[String]]) {
        currentWatch = baseArgSets
        UserDefaults.standard.set(baseArgSets, forKey: "nonya.watchTarget")   // remember so we AUTO-RESUME
        UserDefaults.standard.set(true, forKey: "nonya.watching")             // monitoring across app restarts
        eyesView?.mood = "watching"          // wake up instantly on click (state.json refines it within ~1s)
        baseArgSets.forEach { spawn($0 + modeArgs()) }
        showWatch(); rebuildMenu(running: true)
    }
    // Re-spawn the active watch with the current mode (called when the mode is flipped mid-watch,
    // so the radio choice takes effect immediately instead of only on the next manual start).
    private func restartWatch() {
        guard let base = currentWatch, !procs.isEmpty else { return }
        procs.filter { $0.isRunning }.forEach { $0.terminate() }; procs.removeAll()
        base.forEach { spawn($0 + modeArgs()) }
    }
    // the core does the robust injection itself; the eyes mirror the state.
    private func modeArgs() -> [String] {
        var a = autoMode ? ["--mode", "auto"] : ["--mode", "on-error", "--require-user-idle", "12"]
        if shadowMode { a += ["--shadow"] }      // record-only: decide per mode, send no keys
        return a
    }
    private func appArgs(_ engine: String) -> [String] { ["--target", engine] }
    private func cliArgs(_ engine: String) -> [String] { ["--target", "cli", "--engine", engine] }
    @objc func watchClaudeApp() { start([appArgs("claude")]) }
    @objc func watchClaudeCLI() { start([cliArgs("claude")]) }
    @objc func watchCodexApp()  { start([appArgs("codex")]) }
    @objc func watchCodexCLI()  { start([cliArgs("codex")]) }
    // watch EVERY live session (claude + codex, all windows/projects) via the multi-session
    // scanner — catches a stalled/rate-limited background session the single-target watch misses.
    @objc func watchAllApps()   { start([["--all"]]) }

    // Launch an agent CLI inside a fresh tmux session so nonya can safely recover it on ANY
    // terminal. macOS routes synthetic key events to a terminal's ACTIVE split, not the one we
    // targeted — so raw-split injection (Ghostty/Terminal without tmux) is inherently unreliable
    // and can misfire. Running the agent in tmux gives nonya a PTY-owned pane it can target
    // by id regardless of focus or active split. Opens Terminal.app so the user can interact.
    @objc func launchClaudeTmux() { _launchInTerminal("claude") }
    @objc func launchCodexTmux()  { _launchInTerminal("codex") }

    private func _launchInTerminal(_ engine: String) {
        let (u, prefix) = nonyaBinary()
        // Build the shell path: if the binary is the bundled one use its absolute path (single-quoted
        // for shell safety); otherwise fall back to the PATH name.
        let binShell: String
        if prefix.isEmpty {
            let esc = u.path.replacingOccurrences(of: "'", with: "'\\''")
            binShell = "'\(esc)'"
        } else {
            binShell = "nonya"
        }
        let cmd = "\(binShell) --launch \(engine)"
        // AppleScript: open a new Terminal.app window running the launch command. Terminal is
        // always available on macOS and creates a TTY so tmux attaches interactively.
        // The user sees the agent session; detach any time with C-b d.
        let safeCmd = cmd.replacingOccurrences(of: "\\", with: "\\\\")
                         .replacingOccurrences(of: "\"", with: "\\\"")
        let script = "tell application \"Terminal\"\n  activate\n  do script \"\(safeCmd)\"\nend tell"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", script]
        try? p.run()
    }

    // Intervention mode is a sticky HOW setting (independent of WHICH sessions you watch).
    //  on-error: act only when a session errors/stalls (and only once you've been idle 12s);
    //  auto:     keep every watched session going until it prints <<DONE>> — wakes "잠수" sessions too.
    // Flipping it restarts any active watch so the change applies immediately.
    private func setAuto(_ on: Bool) {
        guard on != autoMode else { return }
        UserDefaults.standard.set(on ? "auto" : "on-error", forKey: "nonya.mode")  // single source of truth
        writeConfig()                       // config.json -> core hot-applies it; keeps Settings popup in sync
        if !procs.isEmpty { restartWatch() } // also re-spawn now so the exact args (idle gate) match instantly
        rebuildMenu(running: !procs.isEmpty)
    }
    @objc func modeOnError() { setAuto(false) }
    @objc func modeAuto()    { setAuto(true) }
    @objc func toggleShadow() {
        UserDefaults.standard.set(!shadowMode, forKey: "nonya.shadow")
        if !procs.isEmpty { restartWatch() }     // apply now: re-spawn with/without --shadow
        rebuildMenu(running: !procs.isEmpty)
    }
    // session list rows are informational; enabled (so they render full-strength, not dimmed grey)
    // but clicking is a harmless no-op. (Used by the "+N more" row and the alert-only tip.)
    @objc func sessionRowNoop() {}

    // CLICK a watched session = "전환": OCR-target that exact project/session in its desktop app and
    // bring it to focus (no typing). In-process (capture needs this app's WindowServer + grants).
    // Doubles as a live test of the M3 targeting: click "노냐" -> the 노냐 conversation should open.
    @objc func focusSession(_ sender: NSMenuItem) {
        guard let info = sender.representedObject as? [String: String] else { _rlog("focusSession: no representedObject"); return }
        let engine = info["engine"] ?? ""
        let sid = info["sid"] ?? ""
        let snippet = info["snippet"] ?? ""
        let lab = info["label"] ?? "session"
        // Claude OCR fallback hint: cwd basename (alias/romanizable), then label, then title.
        var hint = ""
        if let cwd = info["cwd"], !cwd.isEmpty { hint = (cwd as NSString).lastPathComponent }
        if hint.isEmpty { hint = lab.components(separatedBy: ":").first ?? lab }
        if hint.isEmpty { hint = info["title"] ?? "" }
        _postBanner(L10n.t("focus.run.title"), L10n.t("focus.run.body") + " " + lab)
        _rlog("CLICK session: engine=\(engine) sid='\(sid)' hint='\(hint)' snippet='\(snippet.prefix(34))'")
        let h = hint, eng = engine, theSid = sid, theSnip = snippet, theLab = lab
        let appName = (engine == "codex") ? "Codex" : "Claude"
        Task.detached {
            // Try the desktop APP for this session: Codex -> codex:// deep link (launches Codex if
            // needed); Claude -> Vision-OCR resolver (only meaningful when Claude is running).
            func tryApp(launch: Bool) async -> Bool {
                if eng == "codex" {
                    guard !theSid.isEmpty, let url = URL(string: "codex://threads/\(theSid)") else { return false }
                    _rlog("  codex deep-link: \(url.absoluteString)"); NSWorkspace.shared.open(url); return true
                }
                if _runningApp("Claude") == nil {
                    guard launch, let u = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.anthropic.claudefordesktop") else { return false }
                    _rlog("  launch Claude.app (was not running)")
                    return NSWorkspace.shared.open(u)        // launch the app bundle (non-async)
                }
                if h.isEmpty { return false }
                let r = await resolveFocus(appName: "Claude", hint: h)
                _rlog("  claude OCR: \(r)"); return r.hasPrefix("OK")
            }
            func tryCLI() -> Bool {
                guard !theSnip.isEmpty else { return false }
                let tr = _focusTerminalSplit(theSnip); _rlog("  terminal-focus: \(tr)"); return tr == "OK-terminal"
            }
            let appRunning = _runningApp(appName) != nil
            _rlog("  \(appName) running=\(appRunning)")
            // PRIORITY: app first WHEN IT'S RUNNING; else CLI first, then app (launch/deep-link).
            // (no `||` short-circuit — its RHS autoclosure can't carry an `await`.)
            var ok = false
            if appRunning {
                ok = await tryApp(launch: false)
                if !ok { ok = tryCLI() }
            } else {
                ok = tryCLI()
                if !ok { ok = await tryApp(launch: true) }
            }
            _postFocusResult(ok ? "OK" : "NOT-FOUND", theLab)
        }
    }

    // Run the end-to-end recovery self-test (throwaway tmux session) and show the result, so the
    // user can SEE nonya actually recover a stalled session — answering "does this even work?".
    @objc func runSelfTest() {
        let (u, prefix) = nonyaBinary()
        DispatchQueue.global(qos: .userInitiated).async {
            let p = Process(); p.executableURL = u; p.arguments = prefix + ["--selftest"]
            var env = ProcessInfo.processInfo.environment; env["NONYA_LANG"] = L10n.effective
            p.environment = env
            let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
            var out = ""; var passed = false
            do {
                try p.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                out = String(data: data, encoding: .utf8) ?? ""
                passed = (p.terminationStatus == 0)        // exit code, not text -> locale-proof
            } catch { out = "\(error)" }
            // strip the timestamped log lines; keep the human-readable numbered result
            let body = out.split(separator: "\n").filter {
                !$0.contains(" | ") && !$0.isEmpty
            }.joined(separator: "\n")
            DispatchQueue.main.async {
                let a = NSAlert()
                a.messageText = L10n.t("selftest.title")
                a.informativeText = body.isEmpty ? out : body
                a.alertStyle = passed ? .informational : .warning
                a.runModal()
            }
        }
    }

    // LIVE inject test: drives the app IN-PROCESS via injectScold (NSAppleScript run by NonyaPet.app
    // itself), so it uses THE APP'S Accessibility grant — NOT a spawned core whose permission depends
    // on the launching terminal. Raises Claude + types+sends "테스트니 무시하세요".
    @objc func runInjectTest() {
      Task { @MainActor in
        let res = await injectScold(into: "Claude", "테스트니 무시하세요", send: true)
        let isKo = L10n.effective == "ko"
        let msg: String
        if res.hasPrefix("OK") {
            msg = isKo ? "✅ Claude.app을 앞으로 올리고 ‘테스트니 무시하세요’를 입력·전송했습니다."
                       : "✅ Raised Claude and typed+sent ‘테스트니 무시하세요’."
        } else if res.hasPrefix("ABORT-windows") {
            msg = isKo ? "Claude.app 창이 1개가 아닙니다 — 여러 ‘윈도우’는 세션 매핑 불가라 안전상 거부.\n(한 윈도우 안의 탭/화면분할은 OK — 윈도우 자체를 여러 개 띄운 경우만.)"
                       : "Claude has >1 window — multiple windows can't be mapped (tabs/splits in one window are fine)."
        } else if res.hasPrefix("ABORT-noproc") {
            msg = isKo ? "Claude.app이 실행 중이 아닙니다." : "Claude.app is not running."
        } else if res.hasPrefix("ABORT-focus") {
            msg = isKo ? "앱을 전면으로 못 올렸습니다(다른 앱이 강제 전면 중)." : "Could not bring Claude to front."
        } else if res == "AX-ERR" {
            msg = isKo ? "손쉬운 사용 권한이 없습니다. 방금 연 설정 창에서 ‘Nonya’를 켜고 다시 시도하세요." : "Accessibility not granted — enable Nonya in the panel that just opened, then retry."
        } else {
            msg = res
        }
        let a = NSAlert()
        a.messageText = L10n.t("injecttest.title")
        a.informativeText = msg
        a.alertStyle = res.hasPrefix("OK") ? .informational : .warning
        a.runModal()
      }
    }

    // Native metrics dashboard — reads the ledger IN-PROCESS (no slow PyInstaller spawn): charts on
    // top + a scrollable intervention LOG below (WHEN/WHERE/WHAT/verified-OUTCOME). Opens instantly.
    private var metricsWin: NSWindow?
    private var metricsView: MetricsView?
    private var metricsLog: NSTextView?
    private var metricsWindowHours: Double = 24      // metrics VIEW window (12/24/48h, 0=all); ledger keeps all
    private var metricsSeg: NSSegmentedControl?
    private let metricsRanges: [Double] = [12, 24, 48, 0]
    @objc func showMetrics() {
        let W: CGFloat = 600, H: CGFloat = 824, topH: CGFloat = 56, chartsH: CGFloat = 548
        let win = metricsWin ?? {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: W, height: H),
                             styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
            w.title = L10n.t("metrics.title"); w.isReleasedWhenClosed = false; w.level = .floating
            w.minSize = NSSize(width: 520, height: 540)
            w.titlebarAppearsTransparent = true                   // seamless dark titlebar (content stays below it)
            w.appearance = NSAppearance(named: .darkAqua)         // premium dark, appearance-consistent
            w.backgroundColor = NSColor(srgbRed: 0.043, green: 0.047, blue: 0.063, alpha: 1)
            let root = NSView(frame: NSRect(x: 0, y: 0, width: W, height: H))
            root.wantsLayer = true
            root.layer?.backgroundColor = NSColor(srgbRed: 0.043, green: 0.047, blue: 0.063, alpha: 1).cgColor
            root.autoresizingMask = [.width, .height]
            // TOP BAR: time-range segmented control (in-window, no submenu) pinned top.
            let bar = NSView(frame: NSRect(x: 0, y: H - topH, width: W, height: topH))
            bar.autoresizingMask = [.width, .minYMargin]
            let seg = NSSegmentedControl(labels: [L10n.t("metrics.win.12"), L10n.t("metrics.win.24"),
                                                  L10n.t("metrics.win.48"), L10n.t("metrics.win.all")],
                                         trackingMode: .selectOne, target: self, action: #selector(metricsRangeChanged(_:)))
            seg.segmentStyle = .texturedRounded
            seg.sizeToFit()
            seg.frame = NSRect(x: (W - seg.frame.width) / 2, y: (topH - seg.frame.height) / 2 - 2,
                               width: seg.frame.width, height: seg.frame.height)
            seg.autoresizingMask = [.minXMargin, .maxXMargin, .minYMargin]
            bar.addSubview(seg)
            metricsSeg = seg
            // charts pinned below the top bar (fixed height); log scroll fills the rest below.
            let mv = MetricsView(frame: NSRect(x: 0, y: H - topH - chartsH, width: W, height: chartsH))
            mv.autoresizingMask = [.width, .minYMargin]
            let scroll = NSScrollView(frame: NSRect(x: 6, y: 6, width: W - 12, height: H - topH - chartsH - 10))
            scroll.hasVerticalScroller = true; scroll.drawsBackground = false
            scroll.autoresizingMask = [.width, .height]
            let tv = NSTextView(frame: scroll.bounds)
            tv.isEditable = false; tv.drawsBackground = false; tv.textContainerInset = NSSize(width: 18, height: 14)
            tv.autoresizingMask = [.width]
            scroll.documentView = tv
            root.addSubview(mv); root.addSubview(scroll); root.addSubview(bar)
            w.contentView = root; w.center()
            metricsView = mv; metricsLog = tv
            return w
        }()
        metricsWin = win
        if let i = metricsRanges.firstIndex(of: metricsWindowHours) { metricsSeg?.selectedSegment = i }
        reloadMetrics()
        NSApp.activate(ignoringOtherApps: true)
        win.makeKeyAndOrderFront(nil)
    }
    private func reloadMetrics() {
        let wh = metricsWindowHours
        metricsWin?.title = L10n.t("metrics.title") + " — " + (wh > 0 ? L10n.t("metrics.win.\(Int(wh))") : L10n.t("metrics.win.all"))
        metricsView?.m = loadMetrics(windowHours: wh)            // fresh data, in-process (instant)
        if let tv = metricsLog { tv.textStorage?.setAttributedString(loadLogAttributed(windowHours: wh)) }
    }
    // in-window time range picker — re-render the SAME window for the chosen window (no reopen).
    @objc func metricsRangeChanged(_ s: NSSegmentedControl) {
        let i = max(0, min(metricsRanges.count - 1, s.selectedSegment))
        metricsWindowHours = metricsRanges[i]
        reloadMetrics()
    }
    @objc func pickLanguage(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        UserDefaults.standard.set(code, forKey: "nonya.lang")
        rebuildMenu(running: !procs.isEmpty)   // new watches use it immediately; running cores keep their language
    }


    func spawn(_ args: [String]) {
        let (u, prefix) = nonyaBinary(); let p = Process()
        p.executableURL = u; p.arguments = prefix + args
        var env = ProcessInfo.processInfo.environment
        env["NONYA_LANG"] = L10n.effective    // ALWAYS pass the resolved language: a GUI app has no
        if let me = Bundle.main.executablePath { env["NONYA_AX_HELPER"] = me }   // AX split-inject helper = this binary
        // Launching the menu-bar app + starting a watch IS the user's consent to recover (= inject)
        // their real Claude/Codex sessions. The scanner is alert-only for real apps without this; a
        // bare CLI/cron run stays alert-only (the 2026-06-26 safety default). GUI click = opt-in.
        env["NONYA_ALLOW_REAL_APP_INJECT"] = "1"
        p.environment = env                   // LANG/LC_ALL env, so the core would otherwise default to English

        let errPipe = Pipe(); p.standardError = errPipe
        let started = ProcessInfo.processInfo.systemUptime
        p.terminationHandler = { [weak self] proc in   // surface a fast failed start instead of silently reverting to idle
            if proc.terminationStatus != 0 && (ProcessInfo.processInfo.systemUptime - started) < 4.0 {
                let msg = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                DispatchQueue.main.async { self?.coreFailed(msg) }
            }
        }
        do { try p.run(); procs.append(p) } catch { coreFailed("\(error)") }
    }
    private func coreFailed(_ stderr: String) {
        let a = NSAlert(); a.messageText = "nonya: watch could not start"
        a.informativeText = stderr.isEmpty ? "The supervisor core exited immediately." :
            String(stderr.prefix(400))
        a.alertStyle = .warning; a.runModal()
    }
    @objc func stopAll() {
        procs.filter { $0.isRunning }.forEach { $0.terminate() }; procs.removeAll()
        currentWatch = nil                               // no active target -> mode flip won't respawn
        UserDefaults.standard.set(false, forKey: "nonya.watching")   // explicit STOP -> don't auto-resume next launch
        stateTimer?.invalidate()
        eyesView?.mood = "idle"; lastStatus = "idle"     // stopped watching -> back to asleep
        rebuildMenu(running: false)
    }
    func applicationWillTerminate(_ n: Notification) {   // never orphan spawned cores on quit
        procs.filter { $0.isRunning }.forEach { $0.terminate() }
    }

    // MARK: - notifications. The core QUEUES alerts to notifications.jsonl (and skips its own
    // osascript while we're alive); WE post them natively so a click opens the briefing —
    // not Script Editor, which osascript-posted notifications open. Title is branded "노냐?".
    private func notifURL() -> URL { stateURL().deletingLastPathComponent().appendingPathComponent("notifications.jsonl") }
    private func aliveURL() -> URL { stateURL().deletingLastPathComponent().appendingPathComponent(".app-alive") }
    private func setupNotifications() {
        guard Bundle.main.bundleIdentifier != nil else { return }   // UN requires a bundle; raw debug binary skips
        let c = UNUserNotificationCenter.current()
        c.delegate = self
        c.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        if let sz = (try? FileManager.default.attributesOfItem(atPath: notifURL().path))?[.size] as? UInt64 {
            notifOffset = sz            // skip the backlog written before this launch
        }
        touchAppAlive()
        notifTimer = Timer.scheduledTimer(withTimeInterval: 2.5, repeats: true) { [weak self] _ in
            self?.touchAppAlive(); self?.drainNotifications()
        }
    }
    private func touchAppAlive() {
        let url = aliveURL(); let fm = FileManager.default
        try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !fm.fileExists(atPath: url.path) { fm.createFile(atPath: url.path, contents: Data()) }
        try? fm.setAttributes([.modificationDate: Date()], ofItemAtPath: url.path)
    }
    private func drainNotifications() {
        guard let fh = try? FileHandle(forReadingFrom: notifURL()) else { return }
        defer { try? fh.close() }
        let end = fh.seekToEndOfFile()
        if end < notifOffset { notifOffset = 0 }       // truncated/rotated -> restart
        if end == notifOffset { return }
        fh.seek(toFileOffset: notifOffset)
        let data = fh.readDataToEndOfFile(); notifOffset = end
        guard let text = String(data: data, encoding: .utf8) else { return }
        for line in text.split(separator: "\n") where !line.isEmpty {
            guard let d = line.data(using: .utf8),
                  let o = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any] else { continue }
            postNotification(event: (o["title"] as? String) ?? "", body: (o["msg"] as? String) ?? "")
        }
    }
    private func postNotification(event: String, body: String) {
        let content = UNMutableNotificationContent()
        // banner HEADER is the app name = "노냐?" (CFBundleDisplayName). Strip the brand
        // prefix from the event so the bold title is just the state (e.g. "막힘").
        var title = event
        for p in ["nonya:", "노냐?:", "nonya :"] where title.lowercased().hasPrefix(p.lowercased()) {
            title = String(title.dropFirst(p.count)).trimmingCharacters(in: .whitespaces)
        }
        content.title = title.isEmpty ? "노냐?" : title
        content.body = body; content.sound = .default
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }
    func userNotificationCenter(_ c: UNUserNotificationCenter, didReceive r: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        DispatchQueue.main.async { [weak self] in self?.showBriefing() }   // click -> the briefing, not Script Editor
        done()
    }
    func userNotificationCenter(_ c: UNUserNotificationCenter, willPresent n: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound])      // show even when nonya is frontmost
    }
    private var briefWin: NSWindow?
    private var briefBanner: NSTextField?
    private var briefBody: NSTextView?
    // open the window INSTANTLY (loading state) then fill it from a background queue —
    // the core's cold start must never freeze the UI.
    @objc func showBriefing() {
        let win = briefWin ?? makeBriefWindow()
        briefWin = win
        briefBanner?.stringValue = "  " + L10n.t("briefing.loading")
        briefBanner?.textColor = .secondaryLabelColor
        briefBody?.textStorage?.setAttributedString(NSAttributedString(string: ""))
        win.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        let (u, prefix) = briefingBinary()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let p = Process(); p.executableURL = u; p.arguments = prefix + ["--briefing"]
            var e = ProcessInfo.processInfo.environment; e["NONYA_LANG"] = L10n.effective; p.environment = e
            let pipe = Pipe(); p.standardOutput = pipe
            var text = L10n.t("briefing.error")
            do { try p.run(); let d = pipe.fileHandleForReading.readDataToEndOfFile(); p.waitUntilExit()
                 if let s = String(data: d, encoding: .utf8), !s.isEmpty { text = s } } catch {}
            DispatchQueue.main.async { self?.renderBriefing(text) }
        }
    }
    private func makeBriefWindow() -> NSWindow {
        // NORMAL titlebar (no fullSizeContentView) so content sits BELOW the title + traffic
        // lights — never overlapping them. Content view = the area under the titlebar.
        let CH: CGFloat = 540
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 580, height: CH),
                           styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        win.title = L10n.t("win.briefing"); win.isReleasedWhenClosed = false; win.level = .floating
        win.minSize = NSSize(width: 420, height: 320)
        let fx = NSVisualEffectView(frame: NSRect(x: 0, y: 0, width: 580, height: CH))
        fx.material = .underWindowBackground; fx.blendingMode = .behindWindow; fx.state = .active
        fx.autoresizingMask = [.width, .height]
        let banner = NSTextField(labelWithString: "")
        banner.font = .systemFont(ofSize: 16, weight: .bold); banner.lineBreakMode = .byWordWrapping
        banner.maximumNumberOfLines = 3; banner.cell?.wraps = true
        banner.frame = NSRect(x: 20, y: CH - 60, width: 540, height: 48); banner.autoresizingMask = [.width, .minYMargin]
        let sep = NSBox(frame: NSRect(x: 16, y: CH - 70, width: 548, height: 1))
        sep.boxType = .separator; sep.autoresizingMask = [.width, .minYMargin]
        let scroll = NSScrollView(frame: NSRect(x: 8, y: 8, width: 564, height: CH - 80))
        scroll.hasVerticalScroller = true; scroll.drawsBackground = false
        scroll.autoresizingMask = [.width, .height]
        let tv = NSTextView(frame: scroll.bounds)
        tv.isEditable = false; tv.drawsBackground = false; tv.textContainerInset = NSSize(width: 12, height: 10)
        tv.autoresizingMask = [.width]
        scroll.documentView = tv
        fx.addSubview(scroll); fx.addSubview(sep); fx.addSubview(banner)
        win.contentView = fx; win.center()
        briefBanner = banner; briefBody = tv
        return win
    }
    private func renderBriefing(_ text: String) {
        var lines = text.components(separatedBy: "\n")
        var verdict = ""
        let body = NSMutableAttributedString()
        var seenTitle = false
        for raw in lines {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("# ") { seenTitle = true; continue }          // window title covers it
            if verdict.isEmpty && seenTitle && !line.isEmpty && !line.hasPrefix("_") && !line.hasPrefix("#") {
                verdict = line; continue                                     // first content line = the headline
            }
            body.append(briefLine(raw))
        }
        _ = lines
        briefBanner?.stringValue = "  " + (verdict.isEmpty ? L10n.t("briefing.title") : verdict)
        briefBanner?.textColor = briefColor(verdict)
        briefBody?.textStorage?.setAttributedString(body)
    }
    // refined, appearance-aware palette — muted/sophisticated, NOT neon system colors
    private func dyn(_ r1: CGFloat, _ g1: CGFloat, _ b1: CGFloat, _ r2: CGFloat, _ g2: CGFloat, _ b2: CGFloat) -> NSColor {
        NSColor(name: nil) { ap in
            ap.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
                ? NSColor(srgbRed: r2, green: g2, blue: b2, alpha: 1)   // dark mode
                : NSColor(srgbRed: r1, green: g1, blue: b1, alpha: 1)   // light mode
        }
    }
    private var accentNeutral: NSColor { dyn(0.22, 0.42, 0.56, 0.55, 0.74, 0.90) }   // slate blue (working)
    // headline/session color by urgency (en + ko keywords)
    private func briefColor(_ s: String) -> NSColor {
        let l = s.lowercased()
        if l.contains("need") || s.contains("필요") { return dyn(0.74, 0.20, 0.23, 0.95, 0.47, 0.48) }      // coral
        if l.contains("stall") || l.contains("unverified") || s.contains("정체") || s.contains("미검증") {
            return dyn(0.70, 0.46, 0.06, 0.95, 0.73, 0.33) }                                                // amber
        if l.contains("shipped") || l.contains("sleep") || s.contains("완료") || s.contains("잘 자") {
            return dyn(0.12, 0.48, 0.37, 0.42, 0.82, 0.62) }                                                // emerald (calm)
        return NSColor.labelColor
    }
    // lightweight markdown styling -> a readable, sectioned report (no monospace dump)
    private func briefLine(_ raw: String) -> NSAttributedString {
        let t = raw.trimmingCharacters(in: .whitespaces)
        let nl = "\n"
        func a(_ s: String, _ size: CGFloat, _ weight: NSFont.Weight, _ color: NSColor, italic: Bool = false) -> NSAttributedString {
            var font = NSFont.systemFont(ofSize: size, weight: weight)
            if italic { font = NSFontManager.shared.convert(font, toHaveTrait: .italicFontMask) }
            return NSAttributedString(string: s + nl, attributes: [.font: font, .foregroundColor: color])
        }
        if t.hasPrefix("## ") { return a(String(t.dropFirst(3)), 14, .bold, .labelColor) }
        if t.hasPrefix("### ") {
            let s = String(t.dropFirst(4)).replacingOccurrences(of: "_", with: "")
            let c = briefColor(s)
            return a("• " + s, 13, .semibold, c == .labelColor ? accentNeutral : c)
        }
        if t.hasPrefix("- ") || t.hasPrefix("•") { return a("    " + t, 11.5, .regular, .secondaryLabelColor) }
        if t.hasPrefix("_") && t.hasSuffix("_") { return a(t.replacingOccurrences(of: "_", with: ""), 11, .regular, .tertiaryLabelColor, italic: true) }
        if t.isEmpty { return NSAttributedString(string: nl) }
        let clean = t.replacingOccurrences(of: "**", with: "")
        return a(clean, 12, .regular, .labelColor)
    }
    @objc func openAX() {
        if let u = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility") { NSWorkspace.shared.open(u) }
    }

    // MARK: - Settings (writes <state>/config.json; the core hot-applies it every poll = immediate)
    private func cfgDir() -> URL { stateURL().deletingLastPathComponent() }
    private func cfgPayload() -> [String: Any] {
        let d = UserDefaults.standard
        return [
            "sound": (d.object(forKey: "nonya.sound") as? Bool) ?? true,
            "preview_secs": d.integer(forKey: "nonya.preview"),
            "mode": d.string(forKey: "nonya.mode") ?? "on-error",   // default on-error (matches the menu)
            "idle": d.integer(forKey: "nonya.idle"),
            "character": d.string(forKey: "nonya.character") ?? "",
            "lang": L10n.pref == "auto" ? "" : L10n.pref,
            "slack_webhook": d.string(forKey: "nonya.slack") ?? "",
            "telegram_token": d.string(forKey: "nonya.tgtoken") ?? "",
            "telegram_chat": d.string(forKey: "nonya.tgchat") ?? "",
            "ntfy_topic": d.string(forKey: "nonya.ntfy") ?? "",
        ]
    }
    func writeConfig() {
        let dir = cfgDir()
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("config.json")
        if let data = try? JSONSerialization.data(withJSONObject: cfgPayload(), options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: url)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        }
    }
    @objc func settingsChanged(_ sender: Any?) {
        guard let v = settingsWin?.contentView else { return }
        let d = UserDefaults.standard
        if let c = v.viewWithTag(1) as? NSButton { d.set(c.state == .on, forKey: "nonya.sound") }
        if let c = v.viewWithTag(11) as? NSButton { d.set(c.state == .on, forKey: "nonya.autoupdate") }
        if let c = v.viewWithTag(2) as? NSPopUpButton { d.set(["on-error", "auto"][c.indexOfSelectedItem], forKey: "nonya.mode") }
        if let c = v.viewWithTag(3) as? NSTextField { d.set(max(0, min(60, c.integerValue)), forKey: "nonya.preview") }
        if let c = v.viewWithTag(4) as? NSTextField { d.set(max(0, c.integerValue), forKey: "nonya.idle") }
        if let c = v.viewWithTag(5) as? NSPopUpButton { d.set(["", "duck", "cat", "robot"][c.indexOfSelectedItem], forKey: "nonya.character") }
        if let c = v.viewWithTag(7) as? NSTextField { d.set(c.stringValue, forKey: "nonya.slack") }
        if let c = v.viewWithTag(8) as? NSTextField { d.set(c.stringValue, forKey: "nonya.tgtoken") }
        if let c = v.viewWithTag(9) as? NSTextField { d.set(c.stringValue, forKey: "nonya.tgchat") }
        if let c = v.viewWithTag(10) as? NSTextField { d.set(c.stringValue, forKey: "nonya.ntfy") }
        writeConfig()   // running cores re-read config.json each poll -> applies immediately
    }
    @objc func openSettings() {
        writeConfig()   // ensure the file exists with current values
        if let w = settingsWin { w.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        let d = UserDefaults.standard
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 470, height: 482),
                           styleMask: [.titled, .closable], backing: .buffered, defer: false)
        win.title = L10n.t("settings"); win.isReleasedWhenClosed = false; win.level = .floating
        let act = #selector(settingsChanged(_:))
        func field(_ tag: Int, _ val: String, secure: Bool = false, width: CGFloat = 250) -> NSTextField {
            let f: NSTextField = secure ? NSSecureTextField() : NSTextField()
            f.stringValue = val; f.tag = tag; f.target = self; f.action = act
            f.widthAnchor.constraint(equalToConstant: width).isActive = true
            return f
        }
        func popup(_ tag: Int, _ items: [String], _ sel: Int) -> NSPopUpButton {
            let p = NSPopUpButton(); p.tag = tag; p.addItems(withTitles: items)
            p.selectItem(at: max(0, min(items.count - 1, sel))); p.target = self; p.action = act
            return p
        }
        let snd = NSButton(checkboxWithTitle: L10n.t("set.sound"), target: self, action: act); snd.tag = 1
        snd.state = ((d.object(forKey: "nonya.sound") as? Bool) ?? true) ? .on : .off
        let mode = popup(2, [L10n.t("mode.onerror"), L10n.t("mode.auto.full")],
                         ["on-error", "auto"].firstIndex(of: d.string(forKey: "nonya.mode") ?? "on-error") ?? 0)
        let prev = field(3, String(d.integer(forKey: "nonya.preview")), width: 60)
        let idle = field(4, d.integer(forKey: "nonya.idle") > 0 ? String(d.integer(forKey: "nonya.idle")) : "", width: 60)
        let chr = popup(5, [L10n.t("set.default"), "duck", "cat", "robot"],
                        ["", "duck", "cat", "robot"].firstIndex(of: d.string(forKey: "nonya.character") ?? "") ?? 0)
        let upd = NSButton(checkboxWithTitle: L10n.t("set.autoupdate"), target: self, action: act); upd.tag = 11
        upd.state = autoUpdateOn ? .on : .off
        let rows: [(String, NSView)] = [
            ("", snd),
            ("", upd),
            (L10n.t("set.mode"), mode),
            (L10n.t("set.preview"), prev),
            (L10n.t("set.idle"), idle),
            (L10n.t("set.character"), chr),
            (L10n.t("set.slack"), field(7, d.string(forKey: "nonya.slack") ?? "")),
            (L10n.t("set.tgtoken"), field(8, d.string(forKey: "nonya.tgtoken") ?? "", secure: true)),
            (L10n.t("set.tgchat"), field(9, d.string(forKey: "nonya.tgchat") ?? "", width: 150)),
            (L10n.t("set.ntfy"), field(10, d.string(forKey: "nonya.ntfy") ?? "", width: 150)),
        ]
        let stack = NSStackView(); stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 11
        stack.translatesAutoresizingMaskIntoConstraints = false
        for (label, ctl) in rows {
            let r = NSStackView(); r.orientation = .horizontal; r.spacing = 10; r.alignment = .centerY
            let l = NSTextField(labelWithString: label); l.alignment = .right
            l.widthAnchor.constraint(equalToConstant: 150).isActive = true
            r.addArrangedSubview(l); r.addArrangedSubview(ctl); stack.addArrangedSubview(r)
        }
        let note = NSTextField(labelWithString: L10n.t("set.note"))
        note.textColor = .secondaryLabelColor; note.font = .systemFont(ofSize: 11)
        stack.addArrangedSubview(note)
        let host = NSView(frame: win.contentLayoutRect); host.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: host.topAnchor, constant: 20),
            stack.leadingAnchor.constraint(equalTo: host.leadingAnchor, constant: 20),
            stack.trailingAnchor.constraint(lessThanOrEqualTo: host.trailingAnchor, constant: -20),
        ])
        win.contentView = host; win.center(); win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true); settingsWin = win
    }

    // MARK: - GitHub auto-update
    // Checks github.com/ezBuilder/nonya releases for a newer version than this build; when ON (the
    // default) it downloads the NOTARIZED DMG, verifies the Developer-ID signature, and swaps the app
    // in /Applications, then relaunches. No Sparkle/3rd-party dependency. Network only here (a
    // deliberate user-facing check), never on a hot path. Gated by the "자동 업데이트" Settings checkbox.
    private static let updateRepo = "ezBuilder/nonya"
    private var autoUpdateOn: Bool { (UserDefaults.standard.object(forKey: "nonya.autoupdate") as? Bool) ?? true }
    private var appVersion: String { (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String) ?? "0" }

    // semver-ish compare: is `a` strictly newer than `b`? ("v0.2.4" > "0.2.3"); non-numeric tail ignored.
    private func versionNewer(_ a: String, than b: String) -> Bool {
        func parts(_ s: String) -> [Int] {
            let c = s.hasPrefix("v") || s.hasPrefix("V") ? String(s.dropFirst()) : s
            return c.split(separator: ".").map { Int(String($0).prefix { $0.isNumber }) ?? 0 }
        }
        let x = parts(a), y = parts(b)
        for i in 0..<max(x.count, y.count) {
            let xi = i < x.count ? x[i] : 0, yi = i < y.count ? y[i] : 0
            if xi != yi { return xi > yi }
        }
        return false
    }

    func scheduleAutoUpdate() {
        // first check ~15s after launch (let the app settle), then every 6h. The check itself only
        // runs when auto-update is ON; the manual menu item bypasses that.
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in self?.checkForUpdate(manual: false) }
        updateTimer = Timer.scheduledTimer(withTimeInterval: 6 * 3600, repeats: true) { [weak self] _ in
            self?.checkForUpdate(manual: false)
        }
    }

    @objc func checkForUpdateManual() { checkForUpdate(manual: true) }

    func checkForUpdate(manual: Bool) {
        if !manual && !autoUpdateOn { return }                 // auto path is gated; manual always runs
        guard let url = URL(string: "https://api.github.com/repos/\(AppDelegate.updateRepo)/releases/latest") else { return }
        var req = URLRequest(url: url, timeoutInterval: 15)
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("nonya-updater", forHTTPHeaderField: "User-Agent")
        let cur = appVersion
        URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
            guard let self = self else { return }
            guard let data = data,
                  let j = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let tag = j["tag_name"] as? String else {
                if manual { DispatchQueue.main.async { _postBanner(L10n.t("update.fail.title"), L10n.t("update.fail.body")) } }
                return
            }
            guard self.versionNewer(tag, than: cur) else {
                if manual { DispatchQueue.main.async { _postBanner(L10n.t("update.uptodate.title"), L10n.t("update.uptodate.body") + " v\(cur)") } }
                return
            }
            // re-check the toggle right before acting (it may have changed since the timer fired)
            if !manual && !self.autoUpdateOn { return }
            let assets = (j["assets"] as? [[String: Any]]) ?? []
            guard let dmg = assets.first(where: { ($0["name"] as? String)?.lowercased().hasSuffix(".dmg") == true }),
                  let durlStr = dmg["browser_download_url"] as? String, let durl = URL(string: durlStr) else {
                DispatchQueue.main.async { _postBanner(L10n.t("update.available.title"), L10n.t("update.available.body") + " \(tag)") }
                return
            }
            // SECURITY: only ever pull the asset from the official repo's release downloads over https.
            // browser_download_url 302-redirects to GitHub's CDN (URLSession follows it) — pinning the
            // INITIAL url's scheme+host+path is the trust anchor against a tampered/foreign asset URL.
            guard durl.scheme == "https", durl.host == "github.com",
                  durl.path.hasPrefix("/\(AppDelegate.updateRepo)/releases/download/") else {
                if manual { DispatchQueue.main.async { _postBanner(L10n.t("update.fail.title"), L10n.t("update.fail.body")) } }
                return
            }
            // Infinite-loop guard: in the AUTO path, attempt each version AT MOST ONCE. If a prior
            // auto-attempt for this exact tag didn't take effect (e.g. swap failed -> we relaunched the
            // old build), don't re-download it on every launch. The manual menu item always bypasses.
            if !manual && UserDefaults.standard.string(forKey: "nonya.updateAttempt") == tag { return }
            DispatchQueue.main.async { _postBanner(L10n.t("update.downloading.title"), L10n.t("update.downloading.body") + " \(tag)") }
            self.downloadAndApply(durl, version: tag)
        }.resume()
    }

    private func downloadAndApply(_ url: URL, version: String) {
        URLSession.shared.downloadTask(with: url) { [weak self] tmp, resp, _ in
            guard let self = self, let tmp = tmp,
                  (resp as? HTTPURLResponse).map({ $0.statusCode == 200 }) ?? true else { return }
            let cache = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Caches/nonya-update", isDirectory: true)
            try? FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
            let dmg = cache.appendingPathComponent("nonya-\(version).dmg")
            try? FileManager.default.removeItem(at: dmg)
            do { try FileManager.default.moveItem(at: tmp, to: dmg) } catch { return }
            // Burn the per-version attempt marker ONLY now — after a successful download, at the point we
            // are about to quit and swap. A transient download/move failure above returns early and does
            // NOT burn it, so the auto path can retry that version on a later launch.
            UserDefaults.standard.set(version, forKey: "nonya.updateAttempt")
            self.runUpdater(dmgPath: dmg.path)
        }.resume()
    }

    // Detached shell updater: waits for nonya to quit, mounts the DMG, VERIFIES the new bundle's signer
    // identity (Developer-ID Team pin) AND Gatekeeper/notarization, then swaps it into the bundle we are
    // ACTUALLY running (staging copy + atomic rename, with rollback) and relaunches. Runs as an orphaned
    // process (nohup) so it survives our own termination.
    private func runUpdater(dmgPath: String) {
        let cache = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Caches/nonya-update", isDirectory: true)
        let scriptURL = cache.appendingPathComponent("apply-update.sh")
        let target = Bundle.main.bundlePath          // replace the bundle we are running, not a guess
        let teamID = "8YKYNYSV6L"                     // Developer ID Team — pin the signer
        let script = """
        #!/bin/bash
        DMG="\(dmgPath)"; TARGET="\(target)"; TEAMID="\(teamID)"; MNT="/tmp/nonya-update-mnt-$$"
        for i in $(seq 1 100); do /usr/bin/pgrep -x NonyaPet >/dev/null 2>&1 || break; sleep 0.5; done
        sleep 1
        /bin/mkdir -p "$MNT"
        /usr/bin/hdiutil attach "$DMG" -nobrowse -noverify -mountpoint "$MNT" >/dev/null 2>&1 || exit 1
        APP="$MNT/Nonya.app"; OK=0
        # Verify SIGNER IDENTITY + notarization, not just seal integrity: codesign -R pins the Developer
        # ID Team (any other signer is rejected), spctl --assess requires a notarized, Gatekeeper-accepted
        # app. Without these any validly-signed .app would pass --verify and be installed.
        if [ -d "$APP" ] \\
           && /usr/bin/codesign --verify --deep --strict -R "=anchor apple generic and certificate leaf[subject.OU] = \\"$TEAMID\\"" "$APP" >/dev/null 2>&1 \\
           && /usr/sbin/spctl --assess --type execute "$APP" >/dev/null 2>&1; then
          PARENT="$(/usr/bin/dirname "$TARGET")"
          STAGE="$PARENT/.Nonya-new-$$.app"; OLD="$PARENT/.Nonya-old-$$.app"
          /bin/rm -rf "$STAGE" "$OLD" 2>/dev/null
          # Copy the NEW app into a staging dir FIRST (never write over the live bundle mid-copy), then
          # swap by atomic rename on the same volume. A partial ditto leaves only STAGE corrupt; TARGET
          # is untouched until both renames succeed.
          if /usr/bin/ditto "$APP" "$STAGE"; then
            if /bin/mv "$TARGET" "$OLD" 2>/dev/null && /bin/mv "$STAGE" "$TARGET" 2>/dev/null; then
              OK=1; /bin/rm -rf "$OLD" 2>/dev/null
            else
              [ -d "$OLD" ] && [ ! -e "$TARGET" ] && /bin/mv "$OLD" "$TARGET" 2>/dev/null   # rollback
              /bin/rm -rf "$STAGE" 2>/dev/null
            fi
          fi
        fi
        /usr/bin/hdiutil detach "$MNT" >/dev/null 2>&1; /bin/rmdir "$MNT" 2>/dev/null
        # Relaunch whatever bundle now lives at TARGET (new on success, restored-old on failure).
        [ -d "$TARGET" ] && /usr/bin/open "$TARGET"
        """
        do { try script.write(to: scriptURL, atomically: true, encoding: .utf8) } catch { return }
        // launch detached via nohup so it outlives our termination (orphaned to launchd), then quit so
        // it can replace the running bundle.
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = ["-c", "/usr/bin/nohup /bin/bash '\(scriptURL.path)' >/dev/null 2>&1 &"]
        do { try p.run() } catch { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { NSApp.terminate(nil) }
    }

    // MARK: - injection preview (the core writes status="preview" + the pending text; we count down)
    private func previewControl(_ name: String, _ body: String = "") {
        let url = cfgDir().appendingPathComponent(name)
        try? body.data(using: .utf8)?.write(to: url)
        if body.isEmpty { FileManager.default.createFile(atPath: url.path, contents: Data()) }
    }
    @objc private func previewInject() {
        if let tv = previewWin?.contentView?.viewWithTag(20) as? NSTextField, !tv.stringValue.isEmpty {
            previewControl("preview-edit", tv.stringValue)
        }
        previewControl("preview-now"); closePreview()
    }
    @objc private func previewCancelAction() { previewControl("preview-cancel"); closePreview() }
    private func closePreview() { previewTimer?.invalidate(); previewTimer = nil; previewWin?.orderOut(nil) }
    private func showPreview(_ text: String, deadline: Int) {
        if previewWin == nil {
            let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 180),
                               styleMask: [.titled, .closable], backing: .buffered, defer: false)
            win.title = L10n.t("preview.title"); win.isReleasedWhenClosed = false; win.level = .floating
            let field = NSTextField(string: text); field.tag = 20
            field.frame = NSRect(x: 16, y: 96, width: 428, height: 56); field.usesSingleLineMode = false
            let count = NSTextField(labelWithString: ""); count.tag = 21
            count.frame = NSRect(x: 16, y: 60, width: 300, height: 20); count.textColor = .secondaryLabelColor
            let inject = NSButton(title: L10n.t("preview.inject"), target: self, action: #selector(previewInject))
            inject.frame = NSRect(x: 250, y: 14, width: 96, height: 32); inject.keyEquivalent = "\r"
            let cancel = NSButton(title: L10n.t("preview.cancel"), target: self, action: #selector(previewCancelAction))
            cancel.frame = NSRect(x: 350, y: 14, width: 96, height: 32); cancel.keyEquivalent = "\u{1b}"
            let host = NSView(frame: NSRect(x: 0, y: 0, width: 460, height: 180))
            [field, count, inject, cancel].forEach { host.addSubview($0) }
            win.contentView = host; previewWin = win
        }
        previewWin?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        previewTimer?.invalidate()
        previewTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            let left = max(0, deadline - Int(Date().timeIntervalSince1970))
            (self?.previewWin?.contentView?.viewWithTag(21) as? NSTextField)?.stringValue =
                L10n.t("preview.count") + " \(left)s"
            if left <= 0 { self?.closePreview() }
        }
    }

    // live preview picker: a row of cards, each rendering one eye style; click to choose.
    @objc func openStylePicker() {
        if let w = stylePicker { w.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        let styles = EyeStyle.allCases
        let cardW: CGFloat = 132, cardH: CGFloat = 116, pad: CGFloat = 16, labelH: CGFloat = 26
        let w = CGFloat(styles.count) * cardW + CGFloat(styles.count + 1) * pad
        let h = cardH + labelH + pad * 2
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: w, height: h),
                           styleMask: [.titled, .closable], backing: .buffered, defer: false)
        win.title = L10n.t("win.eyestyles")
        win.isReleasedWhenClosed = false
        win.level = .floating
        let content = NSView(frame: NSRect(x: 0, y: 0, width: w, height: h))
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor(white: 0.11, alpha: 1).cgColor
        let sel = EyeStyle.selected
        var cards: [EyeCard] = []
        for (i, s) in styles.enumerated() {
            let x = pad + CGFloat(i) * (cardW + pad)
            let card = EyeCard(frame: NSRect(x: x, y: pad, width: cardW, height: cardH + labelH)) { [weak self] tappedCard in
                EyeStyle.choose(s); self?.eyesView?.style = s        // apply (menubar eyes update live)
                for c in cards { c.layer?.borderWidth = 0 }           // move the selection highlight here; DON'T close
                tappedCard.layer?.borderWidth = 2; tappedCard.layer?.borderColor = NSColor.systemTeal.cgColor
            }
            card.layer?.backgroundColor = NSColor(white: 0.06, alpha: 1).cgColor
            card.layer?.cornerRadius = 12
            if s == sel { card.layer?.borderWidth = 2; card.layer?.borderColor = NSColor.systemTeal.cgColor }
            let ev = EyesView(frame: NSRect(x: 6, y: labelH, width: cardW - 12, height: cardH))
            ev.style = s; ev.mood = "watching"
            card.addSubview(ev)
            let lbl = NSTextField(labelWithString: s.label)
            lbl.frame = NSRect(x: 0, y: 5, width: cardW, height: 18)
            lbl.alignment = .center; lbl.textColor = .white; lbl.font = .systemFont(ofSize: 11, weight: .medium)
            card.addSubview(lbl)
            content.addSubview(card)
            cards.append(card)
        }
        win.contentView = content
        if let scr = NSScreen.main { win.setFrameOrigin(NSPoint(x: scr.frame.midX - w/2, y: scr.frame.midY - h/2)) } else { win.center() }
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        stylePicker = win
    }

    // Inject the scold into a target app — RAISES the app itself (no manual focus needed). Mirrors
    // the proven Python backend: back up clipboard -> raise proc + AXRaise window -> confirm frontmost
    // (abort otherwise, misfire-proof) -> set nudge -> Cmd+V (key code 9) -> delay (Electron input
    // registers the paste) -> Return (key code 36) -> restore clipboard.
    // Inject via ACCESSIBILITY ONLY — no `tell application "System Events"` (that needs a SEPARATE
    // Automation/Apple-Events consent the app may lack; that was the AX-ERR). This uses only the
    // Accessibility grant the app already has: count windows via AXUIElement, raise with
    // NSRunningApplication.activate, set NSPasteboard, then post Cmd+V / Return as HID-level CGEvents
    // (which Electron/Chromium honor, unlike postToPid which it drops).
    @discardableResult
    func injectScold(into proc: String, _ text: String, send: Bool = true, cmdReturn: Bool = false) async -> String {
        guard AXIsProcessTrusted() else { openAX(); return "AX-ERR" }
        guard let app = _runningApp(proc) else { return "ABORT-noproc" }
        let axApp = AXUIElementCreateApplication(app.processIdentifier)
        let wins = (_axAttr(axApp, "AXWindows") as? [AXUIElement]) ?? []
        if wins.count != 1 { return "ABORT-windows:\(wins.count)" }     // can't map multi-window -> session

        let pb = NSPasteboard.general
        let prev = pb.string(forType: .string)
        pb.clearContents(); pb.setString(text, forType: .string)

        // RAISE until the app is actually frontmost — RETRY (don't give up after one try) with both
        // NSRunningApplication.activate AND AXRaise on the window (handles other-Space/fullscreen).
        // CRITICAL: only type AFTER focus is CONFIRMED — otherwise the keystrokes land in whatever
        // is front (e.g. the terminal). If it never comes front -> ABORT, ZERO keys.
        func isFront() -> Bool { NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier }
        var front = false
        for _ in 0..<15 {                                               // ~3s of real raise attempts
            app.activate(options: [.activateAllWindows])
            if let w = (_axAttr(axApp, "AXWindows") as? [AXUIElement])?.first {
                AXUIElementPerformAction(w, "AXRaise" as CFString)
            }
            usleep(200_000)
            if isFront() { front = true; break }
        }
        if !front {
            if let p = prev { pb.clearContents(); pb.setString(p, forType: .string) }
            return "ABORT-focus:\(NSWorkspace.shared.frontmostApplication?.localizedName ?? "?")"
        }
        usleep(150_000)
        let src = CGEventSource(stateID: .combinedSessionState)
        func tap(_ vk: CGKeyCode, cmd: Bool = false) {                  // full keystroke (down+up)
            let d = CGEvent(keyboardEventSource: src, virtualKey: vk, keyDown: true);  if cmd { d?.flags = .maskCommand }; d?.post(tap: .cghidEventTap)
            let u = CGEvent(keyboardEventSource: src, virtualKey: vk, keyDown: false); if cmd { u?.flags = .maskCommand }; u?.post(tap: .cghidEventTap)
        }
        func restore() { if let p = prev { pb.clearContents(); pb.setString(p, forType: .string) } }
        tap(0x09, cmd: true)                                            // Cmd+V (V = 0x09)
        usleep(350_000)
        if !send { usleep(150_000); restore(); return "OK" }
        let head = String(_normName(text).prefix(6))
        // CAPTURE-VERIFY needs Screen Recording. POLL for the pasted text to RENDER (an Electron composer
        // can lag a few hundred ms), taking ONE capture per tick and reusing it to locate the box — so we
        // never take two unsynchronized screenshots. If capture itself is unavailable (no Screen Recording
        // grant), DON'T regress to never-submitting: blind Cmd+Return (the app is already CONFIRMED
        // frontmost above, so keys land in ITS composer; a Cmd+Return on an empty box is a harmless no-op).
        var landed: (found: Bool, at: CGPoint?) = (false, nil)
        var composerNy = 0.0                                            // ny of the text while IN the input box
        var captureWorks = false
        for _ in 0..<3 {
            guard let cap = await _captureOCR(proc) else { break }       // nil -> no Screen Recording
            captureWorks = true
            let hits = cap.runs.filter { $0.ny > 0.80 && _normName($0.text).contains(head) }
            if let r = hits.max(by: { $0.ny < $1.ny }) {                // LOWEST occurrence = the input box
                landed = (true, CGPoint(x: cap.frame.origin.x + r.cx / cap.scale, y: cap.frame.origin.y + r.cy / cap.scale))
                composerNy = r.ny
                break
            }
            usleep(300_000)                                             // give it time to render, re-capture
        }
        if !captureWorks {
            tap(0x24, cmd: true)                                        // blind Electron submit (Cmd+Return)
            usleep(200_000); restore(); return "OK-UNVERIFIED"
        }
        if !landed.found { restore(); return "COMPOSER-VERIFY-FAIL" }   // capture OK but text never rendered -> paste/focus failed
        // SUBMIT with capture-verify. Electron/WebKit Claude/Codex composers SUBMIT on Cmd+Return and
        // treat plain Return as a NEWLINE — the reported "엔터를 제대로 안치고" bug. So LEAD with
        // Cmd+Return, drop to plain Return ONCE as a fallback, and re-check by screenshot that the text
        // LEFT the composer before claiming success. (The old code fired Return THEN Cmd+Return
        // unconditionally with no check — risking a blank re-submit and returning OK even when nothing
        // was sent.)
        func submitKey(_ attempt: Int) {
            if cmdReturn { tap(0x24, cmd: true) }
            else if attempt == 1 { tap(0x24) }                          // fallback: plain Return
            else { tap(0x24, cmd: true) }                               // attempt 0: Cmd+Return
        }
        var sent = false
        for attempt in 0..<2 {                                          // ≤2 distinct keys -> never spam-submit
            if let at = landed.at { _mouseClick(at); usleep(120_000) }   // aim at the box holding our text
            submitKey(attempt)
            usleep(600_000)                                             // capture interval — let it settle
            let still = await _composerText(proc, head)
            // SENT iff the text left the INPUT box: gone, or its lowest occurrence moved UP into the
            // conversation. A copy in the just-sent bubble (still low on screen) must NOT read as unsent.
            if !still.found || still.ny < composerNy - 0.03 { sent = true; break }
        }
        restore()
        return sent ? "OK" : "TYPED-NOT-SUBMITTED"
    }
    @objc func quit() { stopAll(); NSApp.terminate(nil) }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
