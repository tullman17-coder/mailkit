import Foundation
import Combine

/// Phone app stores only the engine URL and bearer token. Mail never leaves the machine running Mailkit.
final class EngineSession: ObservableObject {
    @Published var engineURL: String
    @Published var token: String
    @Published var isConnected: Bool

    init() {
        self.engineURL = UserDefaults.standard.string(forKey: "mailkit.url") ?? "http://127.0.0.1:8765"
        self.token = UserDefaults.standard.string(forKey: "mailkit.token") ?? ""
        self.isConnected = UserDefaults.standard.bool(forKey: "mailkit.connected") && !(UserDefaults.standard.string(forKey: "mailkit.token") ?? "").isEmpty
    }

    func connect(url: String, token: String) {
        let trimmedURL = url.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
        engineURL = trimmedURL
        self.token = trimmedToken
        isConnected = !trimmedURL.isEmpty && !trimmedToken.isEmpty
        UserDefaults.standard.set(trimmedURL, forKey: "mailkit.url")
        UserDefaults.standard.set(trimmedToken, forKey: "mailkit.token")
        UserDefaults.standard.set(isConnected, forKey: "mailkit.connected")
    }

    func disconnect() {
        isConnected = false
        UserDefaults.standard.set(false, forKey: "mailkit.connected")
    }

    func apply(url: URL) {
        guard let pair = Pairing.parse(url) else { return }
        connect(url: pair.url, token: pair.token)
    }
}

enum Pairing {
    static func parse(_ url: URL) -> (url: String, token: String)? {
        guard url.scheme == "mailkit" else { return nil }
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        var engine = ""
        var token = ""
        for item in items {
            if item.name == "url" { engine = item.value ?? "" }
            if item.name == "token" { token = item.value ?? "" }
        }
        if engine.isEmpty || token.isEmpty { return nil }
        return (engine, token)
    }

    static func webURL(engine: String, token: String) -> URL? {
        var parts = URLComponents(string: engine)
        var items = parts?.queryItems ?? []
        items.append(URLQueryItem(name: "token", value: token))
        items.append(URLQueryItem(name: "shell", value: "mobile"))
        parts?.queryItems = items
        return parts?.url
    }
}
