import AppIntents
import Foundation

@MainActor
private enum MitsukoIntentClient {
    static func perform(action: String, fields: [String: Any] = [:]) async throws -> String {
        guard let token = TokenVault.read() else {
            throw CompanionError.message("Open Mitsuko G1 and pair with your laptop first.")
        }

        let discovery = BonjourDiscovery()
        let service = try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<(String, Int), Error>) in
            var finished = false
            discovery.onService = { host, port in
                guard !finished else { return }
                finished = true
                continuation.resume(returning: (host, port))
            }
            discovery.onSearch = {}
            discovery.start()
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(7))
                guard !finished else { return }
                finished = true
                continuation.resume(throwing: CompanionError.message("Could not find Mitsuko G1 on this Wi-Fi. Open the laptop app and try again."))
            }
        }

        guard let base = URL(string: "http://\(service.0):\(service.1)") else {
            throw CompanionError.message("The laptop address is invalid.")
        }
        let url = URL(string: "/api/action", relativeTo: base)!.absoluteURL
        var request = URLRequest(url: url, timeoutInterval: 7)
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        var body = fields
        body["action"] = action
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: request)
        let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code), payload["ok"] as? Bool != false else {
            throw CompanionError.message(payload["error"] as? String ?? "Laptop request failed (\(code)).")
        }
        return payload["message"] as? String ?? "Mitsuko command sent."
    }
}

struct MitsukoScreenSyncIntent: AppIntent {
    static var title: LocalizedStringResource = "Start Screen Sync"
    static var description = IntentDescription("Start Mitsuko G1 screen lighting sync.")
    static var openAppWhenRun = false

    func perform() async throws -> some ReturnsValue<String> {
        .result(value: try await MitsukoIntentClient.perform(action: "screen"))
    }
}

struct MitsukoMusicIntent: AppIntent {
    static var title: LocalizedStringResource = "Start Music Reactive Lighting"
    static var description = IntentDescription("Start Mitsuko G1 music reactive lighting.")
    static var openAppWhenRun = false

    func perform() async throws -> some ReturnsValue<String> {
        .result(value: try await MitsukoIntentClient.perform(action: "music", fields: ["mode": "combined"]))
    }
}

struct MitsukoStopIntent: AppIntent {
    static var title: LocalizedStringResource = "Stop Mitsuko Sync"
    static var description = IntentDescription("Stop the active Mitsuko G1 lighting effect.")
    static var openAppWhenRun = false

    func perform() async throws -> some ReturnsValue<String> {
        .result(value: try await MitsukoIntentClient.perform(action: "stop"))
    }
}

struct MitsukoLightsOffIntent: AppIntent {
    static var title: LocalizedStringResource = "Turn Mitsuko Lights Off"
    static var description = IntentDescription("Turn off Mitsuko G1 lighting.")
    static var openAppWhenRun = false

    func perform() async throws -> some ReturnsValue<String> {
        .result(value: try await MitsukoIntentClient.perform(action: "off"))
    }
}

struct MitsukoShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: MitsukoScreenSyncIntent(), phrases: ["Start screen sync with \(.applicationName)", "Turn on screen sync with \(.applicationName)"], shortTitle: "Screen Sync", systemImageName: "display")
        AppShortcut(intent: MitsukoMusicIntent(), phrases: ["Start music reactive lighting with \(.applicationName)"], shortTitle: "Music Lighting", systemImageName: "music.note")
        AppShortcut(intent: MitsukoStopIntent(), phrases: ["Stop sync with \(.applicationName)"], shortTitle: "Stop Sync", systemImageName: "stop.fill")
        AppShortcut(intent: MitsukoLightsOffIntent(), phrases: ["Turn off lights with \(.applicationName)"], shortTitle: "Lights Off", systemImageName: "lightbulb.slash")
    }
}
