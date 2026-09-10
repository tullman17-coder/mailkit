import Foundation

@main
struct Checks {
    static func main() async throws {
        for value in ["https://mail.example.com", "https://mail.example.com:8443/", "http://127.0.0.1:8765", "http://localhost:8765", "http://[::1]:8765"] {
            _ = try EngineAddress.parse(value)
        }
        for value in ["http://mail.example.com", "file:///etc/passwd", "https://user:secret@mail.example.com", "https://mail.example.com/v1", "https://mail.example.com?token=secret", "https://mail.example.com#x", "", "http://localhost.evil.example"] {
            do { _ = try EngineAddress.parse(value); fatalError("Accepted unsafe engine URL: \(value)") }
            catch is MailError { }
        }
        assert(ProviderPreset.all.count == 11)
        assert(Set(ProviderPreset.all.map(\.id)).count == 11)
        for preset in ProviderPreset.all {
            var form = AccountForm()
            form.password = "previous provider secret"
            form.apply(preset)
            assert(form.password.isEmpty)
            let incoming = form.body["imap"] as! [String: Any]
            let outgoing = form.body["smtp"] as! [String: Any]
            assert(incoming["tls"] as! Bool != incoming["starttls"] as! Bool)
            assert(outgoing["tls"] as! Bool != outgoing["starttls"] as! Bool)
        }
        let fixture = #"{"id":"m1","account_id":"work","native_id":"42","mailbox":"INBOX","subject":"Example","date":"2026-09-10T12:00:00Z","snippet":"Body","from":[{"address":"sender@example.com","name":"Sender"}],"to":[],"reply_to":[],"unread":true,"flagged":false,"has_attachments":false,"body_text":"Body","body_html":null}"#
        let message = try JSONDecoder().decode(MailMessage.self, from: Data(fixture.utf8))
        assert(message.accountID == "work" && message.nativeID == "42" && message.bodyText == "Body")
        assert(message.from.first?.display == "Sender <sender@example.com>")
        if CommandLine.arguments.count > 1 {
            let api = MailAPI(base: try EngineAddress.parse(CommandLine.arguments[1]), token: "local-fixture-token")
            let accounts: [MailAccount] = try await api.request(["accounts"])
            assert(accounts.first?.id == "work@example.com")
            for route in ["provider-error", "token-error", "redirect"] {
                do {
                    let _: [MailAccount] = try await api.request([route])
                    fatalError("Accepted \(route)")
                } catch let error as MailError {
                    if route == "provider-error" { assert(error.message == "Incorrect app password") }
                    if route == "token-error" { assert(error.message.contains("engine rejected")) }
                }
            }
            let echoed: MailAccount = try await api.request(["echo", "work+test@example.com"], query: ["mailbox": "Saved & receipts/2026"], method: "POST", body: ["name": "Example"])
            assert(echoed.id == "work+test@example.com")
        }
        print("Apple checks passed: secure URLs, ten providers + custom, TLS modes, API decoding.")
    }
}
