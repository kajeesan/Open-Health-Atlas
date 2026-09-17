// Copyright (c) 2026 Kajeesan Jeevendra. MIT License.
import AppKit
import WebKit
import UniformTypeIdentifiers

final class AtlasApp: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKScriptMessageHandler, WKDownloadDelegate {
    var window: NSWindow!
    var web: WKWebView?
    var process: Process?
    var input: Pipe?
    var output: Pipe?
    var origin: URL?
    var downloads: [ObjectIdentifier: URL] = [:]
    var generation = 0
    var quitting = false
    var readyBuffer = Data()
    let status = NSTextField(wrappingLabelWithString: "Opening your workspace…")
    let retry = NSButton(title: "Try again", target: nil, action: nil)
    let overlay = NSStackView()

    func applicationDidFinishLaunching(_ notification: Notification) {
        let peers = NSRunningApplication.runningApplications(withBundleIdentifier: "org.openhealthatlas.desktop")
        if let peer = peers.first(where: { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }) {
            peer.activate(options: [.activateAllWindows]); NSApp.terminate(nil); return
        }
        buildMenu()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 850), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Open Health Atlas"; window.minSize = NSSize(width: 800, height: 600)
        window.delegate = self; window.center(); window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        overlay.orientation = .vertical; overlay.spacing = 18; overlay.alignment = .centerX
        status.font = .systemFont(ofSize: 19, weight: .medium); status.alignment = .center
        retry.target = self; retry.action = #selector(start); retry.bezelStyle = .rounded
        overlay.addArrangedSubview(status); overlay.addArrangedSubview(retry)
        overlay.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(overlay)
        NSLayoutConstraint.activate([overlay.centerXAnchor.constraint(equalTo: window.contentView!.centerXAnchor), overlay.centerYAnchor.constraint(equalTo: window.contentView!.centerYAnchor), overlay.widthAnchor.constraint(lessThanOrEqualToConstant: 520)])
        start()
    }

    func buildMenu() {
        let menu = NSMenu(); let appItem = NSMenuItem(); menu.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        appMenu.addItem(withTitle: "About Open Health Atlas", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(.separator()); appMenu.addItem(withTitle: "Quit Open Health Atlas", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let edit = NSMenuItem(); edit.title = "Edit"; menu.addItem(edit); edit.submenu = NSMenu(title: "Edit")
        for (title, action, key) in [("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Cut", "cut:", "x"), ("Select All", "selectAll:", "a")] { edit.submenu!.addItem(withTitle: title, action: Selector(action), keyEquivalent: key) }
        NSApp.mainMenu = menu
    }
    @objc func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "Open Health Atlas", .credits: NSAttributedString(string: "Created by Kajeesan Jeevendra\nMIT License. Bundled third-party notices are included in the application.\nLocal health records and deterministic evidence.")])
    }
    func dataRoot() -> URL {
        // Explicit command-line override supports isolated acceptance workspaces only.
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "--data-root"), args.count > i + 1 { return URL(fileURLWithPath: args[i + 1], isDirectory: true) }
        return FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Open Health Atlas", isDirectory: true)
    }
    @objc func start() {
        let previous = process; stop(); let expected = generation
        overlay.isHidden = false; retry.isHidden = true; status.stringValue = "Opening your workspace…"
        if let previous = previous, previous.isRunning {
            DispatchQueue.global().async { [weak self] in
                for _ in 0..<50 { if !previous.isRunning { break }; Thread.sleep(forTimeInterval: 0.1) }
                if previous.isRunning { previous.terminate() }
                for _ in 0..<30 { if !previous.isRunning { break }; Thread.sleep(forTimeInterval: 0.1) }
                if previous.isRunning { kill(previous.processIdentifier, SIGKILL); previous.waitUntilExit() }
                DispatchQueue.main.async { [weak self] in guard let self = self, self.generation == expected, !self.quitting else { return }; self.launch() }
            }
        } else { launch() }
    }
    func launch() {
        generation += 1; let current = generation
        origin = nil; readyBuffer = Data(); web?.removeFromSuperview(); web = nil
        status.stringValue = "Opening your workspace…"; overlay.isHidden = false; retry.isHidden = true
        guard let resources = Bundle.main.resourceURL else { fail(); return }
        let child = Process(); let stdinPipe = Pipe(); let stdoutPipe = Pipe()
        child.executableURL = resources.deletingLastPathComponent().appendingPathComponent("Resources/PythonRuntime/bin/python3")
        child.arguments = ["-I", "-B", resources.appendingPathComponent("app/desktop/launcher.py").path, "--data-root", dataRoot().path]
        child.currentDirectoryURL = resources.appendingPathComponent("app")
        child.environment = ["HOME": NSHomeDirectory(), "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "OHA_APP_BUNDLE": Bundle.main.bundleURL.path]
        child.standardInput = stdinPipe; child.standardOutput = stdoutPipe
        // Child owns a redacted diagnostic surface; never retain arbitrary stdout/stderr.
        child.standardError = FileHandle.nullDevice
        child.terminationHandler = { [weak self] child in DispatchQueue.main.async { guard let self = self, self.generation == current, !self.quitting else { return }; self.fail("Local service exit \(child.terminationStatus)") } }
        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            DispatchQueue.main.async { guard let self = self, self.generation == current, !data.isEmpty else { return }; self.consume(data) }
        }
        process = child; input = stdinPipe; output = stdoutPipe
        do { try child.run() } catch { let error = error as NSError; fail("\(error.domain) \(error.code)"); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
            guard let self = self, self.generation == current, self.origin == nil else { return }
            self.status.stringValue = "Your workspace is taking a little longer to open. Please keep this window open."
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 120) { [weak self] in
            guard let self = self, self.generation == current, self.origin == nil else { return }
            self.status.stringValue = "Your workspace is still opening. You can keep waiting, or try again."
            self.retry.isHidden = false
        }
    }
    func consume(_ data: Data) {
        readyBuffer.append(data)
        if readyBuffer.count > 16384 { stop(); fail(); return }
        while let newline = readyBuffer.firstIndex(of: 10) {
            let line = readyBuffer.prefix(upTo: newline); readyBuffer.removeSubrange(...newline)
            guard let message = try? JSONSerialization.jsonObject(with: line) as? [String: String], let text = message["url"], let token = message["token"], !token.isEmpty, token.count < 1024,
                  let url = URL(string: text), url.scheme == "http", url.host == "127.0.0.1", let port = url.port, (1024...65535).contains(port), url.path == "/desktop", url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else { continue }
            openWorkspace(url, token: token)
        }
    }
    func openWorkspace(_ url: URL, token: String) {
        origin = url; web?.removeFromSuperview(); web = nil
        overlay.isHidden = false; retry.isHidden = true; status.stringValue = "Opening your workspace…"
        let config = WKWebViewConfiguration(); config.websiteDataStore = .nonPersistent()
        config.userContentController.add(self, name: "oha")
        let pattern = "^" + NSRegularExpression.escapedPattern(for: "http://127.0.0.1:" + String(url.port!) + "/")
        let rules: [[String: Any]] = [ ["trigger": ["url-filter": ".*"], "action": ["type": "block"]], ["trigger": ["url-filter": pattern], "action": ["type": "ignore-previous-rules"]] ]
        let json = String(data: try! JSONSerialization.data(withJSONObject: rules), encoding: .utf8)!
        let current = generation
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "oha-local", encodedContentRuleList: json) { [weak self] list, error in
            guard let self = self, self.generation == current, self.origin == url else { return }
            guard let list = list, error == nil else { self.fail("Local content rules could not be prepared"); return }
            config.userContentController.add(list)
            let view = WKWebView(frame: self.window.contentView!.bounds, configuration: config)
            view.autoresizingMask = [.width, .height]; view.navigationDelegate = self
            self.window.contentView!.addSubview(view, positioned: .below, relativeTo: self.overlay); self.web = view
            var request = URLRequest(url: url.appendingPathComponent("session"))
            request.httpMethod = "POST"; request.setValue(token, forHTTPHeaderField: "X-OHA-Launch-Token")
            view.load(request)
        }
    }
    func allowed(_ url: URL?) -> Bool {
        guard let url = url, let origin = origin else { return false }
        return url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port && url.user == nil && url.password == nil
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) { decisionHandler(allowed(navigationAction.request.url) ? (navigationAction.shouldPerformDownload ? .download : .allow) : .cancel) }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard allowed(navigationResponse.response.url) else { decisionHandler(.cancel); return }
        if let response = navigationResponse.response as? HTTPURLResponse, response.statusCode >= 400 { fail("HTTP \(response.statusCode)" + (response.url?.lastPathComponent == "session" ? " during sign-in" : " while opening the page")); decisionHandler(.cancel); return }
        let attachment = (navigationResponse.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().hasPrefix("attachment") == true
        decisionHandler(attachment || !navigationResponse.canShowMIMEType ? .download : .allow)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        guard allowed(response.url) else { completionHandler(nil); return }
        let panel = NSSavePanel(); panel.nameFieldStringValue = URL(fileURLWithPath: suggestedFilename).lastPathComponent
        panel.beginSheetModal(for: window) { [weak self] result in
            guard result == .OK, let target = panel.url else { completionHandler(nil); return }
            self?.downloads[ObjectIdentifier(download)] = target; completionHandler(target)
        }
    }
    func download(_ download: WKDownload, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, decisionHandler: @escaping (WKDownload.RedirectPolicy) -> Void) {
        decisionHandler(allowed(request.url) ? .allow : .cancel)
    }
    func downloadDidFinish(_ download: WKDownload) { downloads.removeValue(forKey: ObjectIdentifier(download)) }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        downloads.removeValue(forKey: ObjectIdentifier(download))
        let alert = NSAlert(); alert.messageText = "The file could not be saved"; alert.informativeText = "Try exporting it again and choose a writable folder."; alert.beginSheetModal(for: window)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { overlay.isHidden = true }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { let code = error as NSError; if code.code != NSURLErrorCancelled && !(code.domain == "WebKitErrorDomain" && code.code == 102) && retry.isHidden { fail("\(code.domain) \(code.code)") } }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { let code = error as NSError; if code.code != NSURLErrorCancelled && !(code.domain == "WebKitErrorDomain" && code.code == 102) && retry.isHidden { fail("\(code.domain) \(code.code)") } }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { fail("Window content process stopped") }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, allowed(message.frameInfo.request.url), (message.frameInfo.request.url?.path == "/desktop" || message.frameInfo.request.url?.path.hasPrefix("/desktop/") == true),
              let body = message.body as? [String: Any], body["action"] as? String == "chooseDatabase" else { return }
        let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = ["db", "sqlite", "sqlite3"].compactMap { UTType(filenameExtension: $0) }; panel.message = "Choose an existing Open Health Atlas database. A separate copy will be created."
        panel.beginSheetModal(for: window) { [weak self] response in
            guard response == .OK, let path = panel.url?.path, let self = self, let web = self.web, self.allowed(web.url) else { return }
            let data = try! JSONSerialization.data(withJSONObject: ["path": path])
            let detail = String(data: data, encoding: .utf8)!
            web.evaluateJavaScript("window.dispatchEvent(new CustomEvent('oha-database-selected', {detail: \(detail)}));", completionHandler: nil)
        }
    }
    func fail(_ detail: String? = nil) { status.stringValue = "Open Health Atlas could not open this workspace. Your saved data is kept. Try again, or reopen the app." + (detail.map { "\nDetails: " + $0 } ?? ""); web?.isHidden = true; overlay.isHidden = false; retry.isHidden = false }
    func stop() {
        generation += 1
        output?.fileHandleForReading.readabilityHandler = nil
        try? input?.fileHandleForWriting.close(); input = nil; output = nil
        if let child = process, child.isRunning {
            // EOF gives the supervisor time to stop its own workers; only this handle is terminated.
            DispatchQueue.global().asyncAfter(deadline: .now() + 5) { if child.isRunning { child.terminate() } }
        }
        process = nil
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        quitting = true
        let child = process; stop()
        guard let child = child, child.isRunning else { return .terminateNow }
        DispatchQueue.global().async {
            for _ in 0..<50 { if !child.isRunning { break }; Thread.sleep(forTimeInterval: 0.1) }
            if child.isRunning { child.terminate() }
            for _ in 0..<30 { if !child.isRunning { break }; Thread.sleep(forTimeInterval: 0.1) }
            if child.isRunning { kill(child.processIdentifier, SIGKILL) }
            DispatchQueue.main.async { sender.reply(toApplicationShouldTerminate: true) }
        }
        return .terminateLater
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { window?.makeKeyAndOrderFront(nil); return true }
}
let app = NSApplication.shared
let delegate = AtlasApp(); app.delegate = delegate; app.setActivationPolicy(.regular); app.run()
