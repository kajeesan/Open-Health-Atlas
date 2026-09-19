// Copyright (c) 2026 Kajeesan Jeevendra. AGPL-3.0-only.
import Foundation
import Darwin
let ownURL = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
let contents = ownURL.deletingLastPathComponent().deletingLastPathComponent()
let executable = contents.appendingPathComponent("Resources/PythonRuntime/bin/python3").path
let script = contents.appendingPathComponent("Resources/app/desktop/mcp.py").path
let home = NSHomeDirectory()
let environment = ["HOME=\(home)", "PATH=/usr/bin:/bin", "LANG=en_US.UTF-8", "OHA_APP_BUNDLE=\(contents.deletingLastPathComponent().path)"]
let environmentStrings = environment.map { strdup($0) } + [nil]
let arguments = [executable, "-I", "-B", script] + Array(CommandLine.arguments.dropFirst())
let strings = arguments.map { strdup($0) } + [nil]
strings.withUnsafeBufferPointer { buffer in
    environmentStrings.withUnsafeBufferPointer { envBuffer in _ = execve(executable, buffer.baseAddress!, envBuffer.baseAddress!) }
}
fputs("Open Health Atlas bundled runtime could not start. Reinstall the application.\n", stderr)
exit(1)
