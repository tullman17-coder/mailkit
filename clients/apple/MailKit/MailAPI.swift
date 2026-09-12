import AuthenticationServices
import Foundation
import Observation
import Security

private final class NoRedirects: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

struct MailAPI {
    let base: URL
    let token: String
    private static let session = URLSession(configuration: .ephemeral, delegate: NoRedirects(), delegateQueue: nil)

    func request<T: Decodable>(_ path: [String], query: [String: String] = [:], method: String = "GET",
                               body: [String: Any]? = nil) async throws -> T {
        let url = path.reduce(base.appendingPathComponent("v1")) { $0.appendingPathComponent($1) }
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        components.queryItems = query.isEmpty ? nil : query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) }
        var request = URLRequest(url: components.url!)
        request.httpMethod = method
        request.timeoutInterval = 90
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, response) = try await Self.session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw MailError(message: "The engine did not return an HTTP response.") }
        guard let envelope = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              envelope["schema"] as? String == "mailkit.response.v1" else {
            throw MailError(message: "This address did not return the MailKit API (HTTP \(http.statusCode)).")
        }
        guard (200..<300).contains(http.statusCode), envelope["ok"] as? Bool == true else {
            let reason = (envelope["error"] as? [String: Any])?["message"] as? String
            throw MailError(message: reason == "invalid token" ? "The engine rejected this token. Check your connection settings." : reason ?? "The engine could not complete the request.")
        }
        let payload = try JSONSerialization.data(withJSONObject: envelope["data"] ?? NSNull(), options: [.fragmentsAllowed])
        return try JSONDecoder().decode(T.self, from: payload)
    }
}

private struct Ignored: Decodable {}
struct AccountConnectionTest: Decodable {
    let imap, smtp: Bool?
    var summary: String {
        imap == true && smtp == true
            ? "Incoming and outgoing authentication succeeded. No email was sent."
            : "The engine completed its check, but did not confirm both IMAP and SMTP authentication. Update the engine to verify both connections. No email was sent."
    }
}
private struct SavedConnection: Codable { let url, token: String }

@MainActor
private enum ConnectionKeychain {
    static let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "org.zermo.mailkit.connection", kSecAttrAccount as String: "engine"]
    static func read() -> SavedConnection? {
        var value: CFTypeRef?
        let status = SecItemCopyMatching((query.merging([kSecReturnData as String: true]) { _, new in new }) as CFDictionary, &value)
        guard status == errSecSuccess, let data = value as? Data else { return nil }
        return try? JSONDecoder().decode(SavedConnection.self, from: data)
    }
    static func save(_ connection: SavedConnection) throws {
        let data = try JSONEncoder().encode(connection)
        let attrs: [String: Any] = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        var status = SecItemUpdate(query as CFDictionary, attrs as CFDictionary)
        if status == errSecItemNotFound { status = SecItemAdd(query.merging(attrs) { _, new in new } as CFDictionary, nil) }
        guard status == errSecSuccess else { throw MailError(message: "Could not save the connection in Keychain (\(status)).") }
    }
    static func delete() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw MailError(message: "Could not remove the connection from Keychain (\(status)).") }
    }
}

@MainActor
@Observable
final class MailStore {
    var accounts: [MailAccount] = []
    var mailboxes: [MailFolder] = []
    var messages: [MailMessage] = []
    var selectedAccount: MailAccount?
    var selectedFolder: MailFolder?
    var selectedMessage: MailMessage?
    var isConnected = false
    var busy = false
    var error: String?
    var engineURL = ""
    var apiToken = ""
    var searchText = ""
    var messageFilter: MessageFilter = .all
    private var api: MailAPI?
    private var generation = UUID()
    private var messageRequest = UUID()
    private let webAuth = BrowserSignIn()

    init() {
        if let saved = ConnectionKeychain.read() { engineURL = saved.url; apiToken = saved.token }
    }

    func perform(_ operation: @escaping @MainActor () async throws -> Void) {
        guard !busy else { return }
        busy = true
        error = nil
        Task { @MainActor in
            defer { busy = false }
            do { try await operation() }
            catch is CancellationError { }
            catch { self.error = error.localizedDescription }
        }
    }

    private func client() throws -> MailAPI {
        guard let api else { throw MailError(message: "Connect to your MailKit engine first.") }
        return api
    }

    func connect(url: String, token: String) async throws {
        let base = try EngineAddress.parse(url)
        let token = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !token.isEmpty, !token.contains(where: { $0.isWhitespace || $0.isNewline }) else {
            throw MailError(message: "Enter the bearer token from your engine.")
        }
        let candidate = MailAPI(base: base, token: token)
        let accounts: [MailAccount] = try await candidate.request(["accounts"])
        try ConnectionKeychain.save(SavedConnection(url: base.absoluteString, token: token))
        generation = UUID()
        api = candidate
        engineURL = base.absoluteString
        apiToken = token
        self.accounts = accounts
        mailboxes = []; messages = []; selectedAccount = nil; selectedFolder = nil; selectedMessage = nil
        isConnected = true
        error = nil
        if let account = accounts.first {
            try await selectAccount(account)
        }
    }

    func reconnectSavedEngine() {
        guard !isConnected, !busy, !engineURL.isEmpty, !apiToken.isEmpty else { return }
        let savedURL = engineURL
        let savedToken = apiToken
        perform { try await self.connect(url: savedURL, token: savedToken) }
    }

    func disconnect() {
        do { try ConnectionKeychain.delete() }
        catch { self.error = error.localizedDescription; return }
        generation = UUID()
        webAuth.cancel()
        api = nil; apiToken = ""; isConnected = false
        accounts = []; mailboxes = []; messages = []
        selectedAccount = nil; selectedFolder = nil; selectedMessage = nil
    }

    func loadAccounts() async throws {
        let stamp = generation
        let rows: [MailAccount] = try await client().request(["accounts"])
        guard stamp == generation else { return }
        accounts = rows
    }

    func selectAccount(_ account: MailAccount) async throws {
        let stamp = generation
        messageRequest = UUID()
        selectedAccount = account; selectedFolder = nil; selectedMessage = nil
        mailboxes = []; messages = []; searchText = ""
        let rows: [MailFolder] = try await client().request(["mailboxes"], query: ["account": account.id])
        guard stamp == generation, selectedAccount?.id == account.id else { return }
        let folders = rows.filter(\.selectable)
        mailboxes = folders
        guard let folder = folders.first(where: { $0.role == "inbox" }) ?? MailFolder.railOrdered(folders).first else { return }
        try await selectFolder(folder)
    }

    func selectFolder(_ folder: MailFolder) async throws {
        selectedFolder = folder; selectedMessage = nil; messages = []; searchText = ""
        try await loadMessages()
    }

    func loadMessages() async throws {
        guard let account = selectedAccount, let folder = selectedFolder else { return }
        let stamp = generation
        let request = UUID()
        messageRequest = request
        let search = searchText
        let filter = messageFilter
        // ponytail: sort the latest 100 matches; add cursor pagination for older-mail browsing.
        var query = ["account": account.id, "mailbox": folder.name, "query": search, "limit": "100"]
        query.merge(filter.query) { _, new in new }
        let rows: [MailMessage] = try await client().request(["messages"], query: query)
        guard stamp == generation, request == messageRequest,
              selectedAccount?.id == account.id, selectedFolder?.id == folder.id,
              search == searchText, filter == messageFilter else { return }
        messages = rows
        if let selected = selectedMessage, !rows.contains(where: { $0.id == selected.id }) {
            selectedMessage = nil
        }
    }

    func setMessageFilter(_ filter: MessageFilter) async throws {
        messageFilter = filter
        messages = []; selectedMessage = nil
        try await loadMessages()
    }

    func openMessage(_ message: MailMessage) async throws {
        let stamp = generation
        selectedMessage = message
        let full: MailMessage = try await client().request(["messages", message.id], query: ["account": message.accountID, "mailbox": message.mailbox])
        guard stamp == generation, selectedMessage?.id == message.id else { return }
        selectedMessage = full
    }

    func mutateMessage(_ message: MailMessage, action: String, mailbox: String? = nil) async throws {
        guard ["read", "unread", "flag", "unflag", "move"].contains(action) else { throw MailError(message: "Unsupported message action.") }
        var body: [String: Any] = ["native_id": message.nativeID]
        if let mailbox { body["mailbox"] = mailbox }
        let _: Ignored = try await client().request(["messages", message.id, action], query: ["account": message.accountID, "mailbox": message.mailbox], method: "POST", body: body)
        if action == "move" { selectedMessage = nil }
        try await loadMessages()
        if selectedMessage?.id == message.id, let updated = messages.first(where: { $0.id == message.id }) { try await openMessage(updated) }
    }

    func send(accountID: String, to: String, subject: String, text: String, replyTo: String? = nil) async throws {
        let recipients = to.split(separator: ",").map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        guard !recipients.isEmpty, recipients.allSatisfy({ $0.contains("@") && !$0.contains("\n") && !$0.contains("\r") }) else {
            throw MailError(message: "Enter a valid recipient address. Separate multiple addresses with commas.")
        }
        var body: [String: Any] = ["account": accountID, "to": recipients, "subject": subject, "text": text]
        if let replyTo { body["id"] = replyTo }
        // SMTP sends are never automatically retried: a lost response can mean delivery succeeded.
        let _: Ignored = try await client().request([replyTo == nil ? "send" : "reply"], method: "POST", body: body)
    }

    func addAccount(_ form: AccountForm) async throws {
        let api = try client()
        let stamp = generation
        if form.preset.requiresOAuth {
            let clientID = form.clientID.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !clientID.isEmpty else { throw MailError(message: "Configure the provider's registered OAuth client ID first.") }
            let scheme = form.providerID == "gmail" ? clientID.split(separator: ".").reversed().joined(separator: ".") : "org.zermo.mailkit"
            let redirect = form.providerID == "gmail" ? "\(scheme):/oauthredirect" : "\(scheme)://oauth/callback"
            struct Start: Decodable { let authorization_url, state: String }
            let start: Start = try await api.request(["oauth", "begin"], method: "POST", body: ["address": form.address.trimmingCharacters(in: .whitespacesAndNewlines), "name": form.name, "provider": form.providerID == "gmail" ? "gmail" : "graph", "client_id": clientID, "redirect_uri": redirect])
            guard let url = URL(string: start.authorization_url), url.scheme == "https",
                  ["accounts.google.com", "login.microsoftonline.com"].contains(url.host ?? "") else {
                throw MailError(message: "The engine returned an unexpected sign-in address.")
            }
            let callback = try await webAuth.signIn(url: url, scheme: scheme)
            guard stamp == generation else { throw CancellationError() }
            let _: MailAccount = try await api.request(["oauth", "complete"], method: "POST", body: ["state": start.state, "callback_url": callback.absoluteString])
        } else {
            guard !form.password.isEmpty else { throw MailError(message: "Enter your provider's app password or mailbox password.") }
            let _: Ignored = try await api.request(["accounts", "validate"], method: "POST", body: form.body)
            guard stamp == generation else { throw CancellationError() }
            let _: MailAccount = try await api.request(["accounts"], method: "POST", body: form.body)
        }
        try await loadAccounts()
    }

    func testAccount(_ account: MailAccount) async throws -> String {
        let result: AccountConnectionTest = try await client().request(["accounts", account.id, "test"], method: "POST", body: [:])
        return result.summary
    }
    func removeAccount(_ account: MailAccount) async throws {
        let _: Ignored = try await client().request(["accounts", account.id], method: "DELETE")
        if selectedAccount?.id == account.id { selectedAccount = nil; selectedFolder = nil; selectedMessage = nil; mailboxes = []; messages = [] }
        try await loadAccounts()
    }
    func loadRules() async throws -> [MailRule] { try await client().request(["rules"]) }
    func addRule(name: String, subject: String, tag: String, move: String, accountID: String) async throws {
        guard !name.isEmpty, !subject.isEmpty, !accountID.isEmpty, !tag.isEmpty || !move.isEmpty else {
            throw MailError(message: "Choose an account, name, subject match, and at least one action.")
        }
        var actions: [String: Any] = [:]
        if !tag.isEmpty { actions["tag"] = [tag] }
        if !move.isEmpty { actions["move"] = move }
        let _: Ignored = try await client().request(["rules"], method: "POST", body: ["name": name, "match": ["account": [accountID], "subject_contains": [subject]], "actions": actions])
    }
    func removeRule(_ rule: MailRule) async throws {
        let _: Ignored = try await client().request(["rules", rule.id], method: "DELETE")
    }
}

@MainActor
private final class BrowserSignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?
    func signIn(url: URL, scheme: String) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            let auth = ASWebAuthenticationSession(url: url, callbackURLScheme: scheme) { callback, error in
                if let callback { continuation.resume(returning: callback) }
                else { continuation.resume(throwing: error ?? MailError(message: "Sign-in was cancelled.")) }
            }
            auth.presentationContextProvider = self
            auth.prefersEphemeralWebBrowserSession = true
            session = auth
            if !auth.start() { session = nil; continuation.resume(throwing: MailError(message: "Could not open the system sign-in browser.")) }
        }
    }
    func cancel() { session?.cancel(); session = nil }
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        #if os(macOS)
        return NSApplication.shared.keyWindow ?? ASPresentationAnchor()
        #else
        return UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows).first(where: \.isKeyWindow) ?? ASPresentationAnchor()
        #endif
    }
}
