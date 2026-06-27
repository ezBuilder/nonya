import AppKit

final class ProbeApp: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    private var windows: [NSWindow] = []
    private var textViews: [NSTextView] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMenu()
        let args = CommandLine.arguments
        let windowCount = args.contains("--multi") ? 2 : 1
        for index in 0..<windowCount {
            makeWindow(index: index)
        }
        windows.first?.makeKeyAndOrderFront(nil)
        if let firstWindow = windows.first, let firstText = textViews.first {
            firstWindow.makeFirstResponder(firstText)
        }
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            self.focusFirstTextView()
        }
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        focusFirstTextView()
    }

    private func focusFirstTextView() {
        guard let firstWindow = windows.first, let firstText = textViews.first else { return }
        firstWindow.makeKeyAndOrderFront(nil)
        firstWindow.makeFirstResponder(firstText)
    }

    private func installMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editItem.submenu = editMenu
        NSApp.mainMenu = main
    }

    private func makeWindow(index: Int) {
        let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 520, height: 260))
        text.string = "NONYA_PROBE_READY_\(index)"
        text.isEditable = true
        text.isSelectable = true
        text.delegate = self

        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 520, height: 260))
        scroll.hasVerticalScroller = true
        scroll.documentView = text

        let window = NSWindow(
            contentRect: NSRect(x: 120 + index * 36, y: 180 - index * 36, width: 520, height: 260),
            styleMask: [.titled, .closable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "NonyaProbe-\(index)"
        window.contentView = scroll
        window.initialFirstResponder = text
        window.makeFirstResponder(text)
        textViews.append(text)
        windows.append(window)
        window.orderFront(nil)
    }
}

let app = NSApplication.shared
let delegate = ProbeApp()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
