import Foundation
import Combine
import Security

@MainActor
final class CompanionModel: ObservableObject {
    @Published private(set) var laptopFound = false
    @Published private(set) var connected = false
    @Published private(set) var mode = "idle"
    @Published private(set) var profiles: [String] = []
    @Published private(set) var message = "Looking for your laptop on Wi-Fi…"
    @Published private(set) var isWorking = false
    @Published var showPairing = false

    private let discovery = BonjourDiscovery()
    private var baseURL: URL?
    private var token = TokenVault.read()
    private var refreshTask: Task<Void, Never>?

    var needsPairing: Bool { token == nil }

    init() {
        discovery.onService = { [weak self] host, port in
            Task { @MainActor in
                guard let self else { return }
                guard let url = URL(string: "http://\(host):\(port)") else { return }
                self.baseURL = url
                self.laptopFound = true
                if self.token == nil {
                    self.connected = false
                    self.message = "Laptop found · pair once to connect"
                } else {
                    await self.refresh()
                }
            }
        }
        discovery.onSearch = { [weak self] in
            Task { @MainActor in
                guard let self, !self.laptopFound else { return }
                self.message = "Looking for your laptop on Wi-Fi…"
            }
        }
        discovery.start()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard let self, !Task.isCancelled else { return }
                await self.refresh()
            }
        }
    }

    func refresh() async {
        guard let baseURL, let token else { return }
        do {
            let status = try await request(path: "/api/status", base: baseURL, bearer: token)
            connected = true
            mode = status["mode"] as? String ?? "idle"
            message = (status["deviceConnected"] as? Bool == true)
                ? "Laptop connected · G1 detected"
                : "Laptop connected · G1 not detected"
            if let result = try? await request(path: "/api/profiles", base: baseURL, bearer: token) {
                profiles = result["profiles"] as? [String] ?? []
            }
        } catch CompanionError.unauthorized {
            TokenVault.delete()
            token = nil
            connected = false
            profiles = []
            message = "This iPhone’s saved pairing expired. Pair it again with the laptop."
            showPairing = true
        } catch {
            connected = false
            message = laptopFound ? "Laptop found · reconnecting…" : "Looking for your laptop on Wi-Fi…"
        }
    }

    func pair(code: String) async -> Bool {
        guard let baseURL else {
            message = "Keep the laptop and iPhone on the same Wi-Fi, then try again."
            return false
        }
        isWorking = true
        defer { isWorking = false }
        do {
            let result = try await request(path: "/api/pair", base: baseURL, bearer: nil,
                                           body: ["pin": code])
            guard let newToken = result["token"] as? String, !newToken.isEmpty else {
                throw CompanionError.message("The laptop did not return a pairing token.")
            }
            try TokenVault.save(newToken)
            token = newToken
            await refresh()
            showPairing = false
            return true
        } catch {
            message = error.localizedDescription
            return false
        }
    }

    func send(_ action: String, fields: [String: Any] = [:]) async {
        guard let baseURL, let token else {
            if laptopFound { showPairing = true }
            else { message = "Open Mitsuko G1 on the laptop and connect both devices to Wi-Fi." }
            return
        }
        isWorking = true
        defer { isWorking = false }
        do {
            var body = fields
            body["action"] = action
            let result = try await request(path: "/api/action", base: baseURL, bearer: token, body: body)
            message = result["message"] as? String ?? "Command sent"
            await refresh()
        } catch {
            connected = false
            message = error.localizedDescription
        }
    }

    private func request(path: String, base: URL, bearer: String?,
                         body: [String: Any]? = nil) async throws -> [String: Any] {
        let url = URL(string: path, relativeTo: base)!.absoluteURL
        var request = URLRequest(url: url, timeoutInterval: 5)
        request.httpMethod = body == nil ? "GET" : "POST"
        if let bearer { request.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization") }
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        if code == 401 {
            throw CompanionError.unauthorized
        }
        guard (200..<300).contains(code), payload["ok"] as? Bool != false else {
            throw CompanionError.message(payload["error"] as? String ?? "Laptop request failed (\(code)).")
        }
        return payload
    }
}

enum CompanionError: LocalizedError {
    case unauthorized
    case message(String)
    var errorDescription: String? {
        switch self {
        case .unauthorized: return "This iPhone’s saved pairing expired. Pair it again with the laptop."
        case let .message(text): return text
        }
    }
}

final class BonjourDiscovery: NSObject, NetServiceBrowserDelegate, NetServiceDelegate {
    var onService: ((String, Int) -> Void)?
    var onSearch: (() -> Void)?
    private let browser = NetServiceBrowser()
    private var services: [NetService] = []

    override init() {
        super.init()
        browser.delegate = self
    }

    func start() {
        browser.searchForServices(ofType: "_mitsukog1._tcp.", inDomain: "local.")
    }

    func netServiceBrowserWillSearch(_ browser: NetServiceBrowser) { onSearch?() }

    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        services.append(service)
        service.delegate = self
        service.resolve(withTimeout: 6)
    }

    func netServiceDidResolveAddress(_ sender: NetService) {
        guard let rawHost = sender.hostName, sender.port > 0 else { return }
        let host = rawHost.trimmingCharacters(in: CharacterSet(charactersIn: "."))
        onService?(host, sender.port)
    }

    func netService(_ sender: NetService, didNotResolve errorDict: [String: NSNumber]) {
        sender.resolve(withTimeout: 6)
    }
}

enum TokenVault {
    private static let service = "com.mitsukog1.ios"
    private static let account = "laptop-access-token"

    static func read() -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account,
                                    kSecReturnData as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func save(_ token: String) throws {
        let data = Data(token.utf8)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account]
        let update: [String: Any] = [kSecValueData as String: data,
                                     kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var add = query
            update.forEach { add[$0.key] = $0.value }
            let result = SecItemAdd(add as CFDictionary, nil)
            guard result == errSecSuccess else { throw CompanionError.message("Could not securely save the pairing.") }
        } else if status != errSecSuccess {
            throw CompanionError.message("Could not securely save the pairing.")
        }
    }

    static func delete() {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: service,
                                    kSecAttrAccount as String: account]
        SecItemDelete(query as CFDictionary)
    }
}
