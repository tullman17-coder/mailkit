import Foundation

struct MailAccount: Decodable, Identifiable, Hashable {
    let id, name, address, provider, auth: String
}

struct MailFolder: Decodable, Identifiable, Hashable {
    var id: String { name }
    let name, role: String
    let unseen: Int?
    let selectable: Bool

    var railTitle: String {
        switch role {
        case "inbox": "Inbox"
        case "junk": "Spam"
        case "trash": "Deleted"
        case "sent": "Sent"
        case "drafts": "Drafts"
        case "archives": name.localizedCaseInsensitiveContains("all mail") ? "All Mail" : "Archive"
        case "saved": "Starred"
        default: name
        }
    }

    private var railRank: Int {
        switch role {
        case "inbox": 0
        case "junk": 1
        case "trash": 2
        case "sent": 3
        case "drafts": 4
        case "archives": 5
        case "saved": 6
        default: 7
        }
    }

    static func railOrdered(_ folders: [MailFolder]) -> [MailFolder] {
        folders.sorted {
            if $0.railRank != $1.railRank { return $0.railRank < $1.railRank }
            return $0.railTitle.localizedStandardCompare($1.railTitle) == .orderedAscending
        }
    }
}

struct MailAddress: Decodable, Hashable {
    let address, name: String
    var display: String { name.isEmpty ? address : "\(name) <\(address)>" }
}

struct MailMessage: Decodable, Identifiable, Hashable {
    let id, accountID, nativeID, mailbox, subject, date, snippet: String
    let from, to, replyTo: [MailAddress]
    let bodyText, bodyHTML: String?
    let internalDate: String?
    let unread, flagged, hasAttachments: Bool
    enum CodingKeys: String, CodingKey {
        case id, mailbox, subject, date, snippet, from, to, unread, flagged
        case accountID = "account_id", nativeID = "native_id", replyTo = "reply_to"
        case bodyText = "body_text", bodyHTML = "body_html", hasAttachments = "has_attachments"
        case internalDate = "internal_date"
    }

    var timestamp: Date? {
        for fractional in [false, true] {
            if let value = try? Date(date, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: fractional)) { return value }
        }
        // A missing/invalid sender date falls back to the server's received date.
        guard let internalDate else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "d-MMM-yyyy HH:mm:ss Z"
        return formatter.date(from: internalDate.trimmingCharacters(in: .whitespaces))
    }

    var displayDate: String { timestamp?.formatted(date: .abbreviated, time: .shortened) ?? "Unknown date" }
}

enum MessageFilter: String, CaseIterable {
    case all, unread, flagged, unreadAndFlagged
    var title: String {
        switch self {
        case .all: "All mail"
        case .unread: "Unread"
        case .flagged: "Flagged"
        case .unreadAndFlagged: "Unread & flagged"
        }
    }
    var query: [String: String] {
        var values: [String: String] = [:]
        if self == .unread || self == .unreadAndFlagged { values["unread"] = "true" }
        if self == .flagged || self == .unreadAndFlagged { values["flagged"] = "true" }
        return values
    }
}

enum MessageSort: String, CaseIterable {
    case newest, oldest, sender, subject
    var title: String {
        switch self {
        case .newest: "Newest first"
        case .oldest: "Oldest first"
        case .sender: "Sender A–Z"
        case .subject: "Subject A–Z"
        }
    }
    func apply(to messages: [MailMessage]) -> [MailMessage] {
        // Parse once per message, not once per comparison. Undated mail always goes last.
        messages.map { (message: $0, date: $0.timestamp) }.sorted { left, right in
            let a = left.message, b = right.message
            if self == .sender || self == .subject {
                let first = self == .sender ? a.from.map(\.display).joined(separator: ", ") : a.subject
                let second = self == .sender ? b.from.map(\.display).joined(separator: ", ") : b.subject
                let order = first.localizedStandardCompare(second)
                if order != .orderedSame { return order == .orderedAscending }
            }
            if let first = left.date, let second = right.date, first != second {
                return self == .oldest ? first < second : first > second
            }
            if (left.date == nil) != (right.date == nil) { return right.date == nil }
            if let first = UInt64(a.nativeID), let second = UInt64(b.nativeID), first != second {
                return self == .oldest ? first < second : first > second
            }
            return a.id < b.id
        }.map(\.message)
    }
}

struct MailRule: Decodable, Identifiable {
    let id, name: String
    let enabled: Bool
}

struct ProviderPreset: Identifiable {
    let id, name, imapHost, smtpHost: String
    var smtpPort = 587
    var smtpTLS = false
    var requiresOAuth = false
    let note: String
    static let all: [Self] = [
        .init(id: "gmail", name: "Google / Gmail", imapHost: "imap.gmail.com", smtpHost: "smtp.gmail.com", requiresOAuth: true, note: "Sign in with a registered Google iOS OAuth client. Workspace policy may require administrator approval."),
        .init(id: "microsoft", name: "Outlook / Microsoft 365", imapHost: "outlook.office365.com", smtpHost: "smtp-mail.outlook.com", requiresOAuth: true, note: "Sign in with a registered Microsoft app. IMAP and SMTP AUTH must be enabled. Business accounts use smtp.office365.com."),
        .init(id: "icloud", name: "iCloud Mail", imapHost: "imap.mail.me.com", smtpHost: "smtp.mail.me.com", note: "Create an app-specific password in your Apple Account. Use your full email address."),
        .init(id: "yahoo", name: "Yahoo Mail", imapHost: "imap.mail.yahoo.com", smtpHost: "smtp.mail.yahoo.com", note: "Generate an app password in Yahoo Account Security."),
        .init(id: "aol", name: "AOL Mail", imapHost: "imap.aol.com", smtpHost: "smtp.aol.com", smtpPort: 465, smtpTLS: true, note: "Use an AOL app password."),
        .init(id: "fastmail", name: "Fastmail", imapHost: "imap.fastmail.com", smtpHost: "smtp.fastmail.com", smtpPort: 465, smtpTLS: true, note: "Create an app password with mail access. Basic plans do not include IMAP/SMTP."),
        .init(id: "zoho", name: "Zoho Mail", imapHost: "imap.zoho.com", smtpHost: "smtp.zoho.com", smtpPort: 465, smtpTLS: true, note: "Enable IMAP and use an app password with two-factor authentication. Check your plan and data center; paid organizations may use imappro/smtppro."),
        .init(id: "gmx", name: "GMX", imapHost: "imap.gmx.com", smtpHost: "mail.gmx.com", smtpPort: 465, smtpTLS: true, note: "Enable IMAP in GMX settings. Use an app password when required; regional server names may differ."),
        .init(id: "mailcom", name: "mail.com", imapHost: "imap.mail.com", smtpHost: "smtp.mail.com", smtpPort: 465, smtpTLS: true, note: "IMAP requires an eligible Premium account. Enable IMAP in your account settings."),
        .init(id: "ionos", name: "IONOS", imapHost: "imap.ionos.com", smtpHost: "smtp.ionos.com", smtpPort: 465, smtpTLS: true, note: "Use your mailbox password and the regional server names shown by IONOS."),
        .init(id: "other", name: "Other IMAP / SMTP", imapHost: "", smtpHost: "", note: "Enter the server addresses supplied by your email provider. Both connections require TLS. Proton needs a desktop Bridge; Tuta does not support IMAP/SMTP.")
    ]
}

struct AccountForm {
    var name = "", address = "", username = "", password = "", clientID = ""
    var smtpUsername = "", smtpPassword = ""
    var providerID = "icloud"
    var imapHost = "imap.mail.me.com", smtpHost = "smtp.mail.me.com"
    var imapPort = 993, smtpPort = 587
    var imapSTARTTLS = false, smtpTLS = false
    var preset: ProviderPreset { ProviderPreset.all.first { $0.id == providerID } ?? ProviderPreset.all.last! }
    mutating func apply(_ preset: ProviderPreset) {
        providerID = preset.id
        imapHost = preset.imapHost
        smtpHost = preset.smtpHost
        imapPort = 993
        smtpPort = preset.smtpPort
        imapSTARTTLS = false
        smtpTLS = preset.smtpTLS
        password = ""
        smtpUsername = ""; smtpPassword = ""
    }
    var body: [String: Any] {
        ["address": address.trimmingCharacters(in: .whitespacesAndNewlines), "name": name,
         "provider": providerID == "yahoo" ? "yahoo" : "imap", "auth": "app_password",
         "username": username.isEmpty ? address.trimmingCharacters(in: .whitespacesAndNewlines) : username,
         "password": password, "smtp_username": smtpUsername, "smtp_password": smtpPassword, "discover": false,
         "imap": ["host": imapHost, "port": imapPort, "tls": !imapSTARTTLS, "starttls": imapSTARTTLS],
         "smtp": ["host": smtpHost, "port": smtpPort, "tls": smtpTLS, "starttls": !smtpTLS]]
    }
}

struct MailError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

enum EngineAddress {
    static func parse(_ text: String) throws -> URL {
        guard let components = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil,
              components.query == nil, components.fragment == nil,
              components.path.isEmpty || components.path == "/", let url = components.url else {
            throw MailError(message: "Enter the engine's base address without a path, password, or query.")
        }
        // Local HTTP supports the Mac engine and simulator; physical iOS connections use HTTPS.
        guard components.scheme == "https" || (components.scheme == "http" && ["localhost", "127.0.0.1", "[::1]"].contains(host)) else {
            throw MailError(message: "Use HTTPS for the engine connection. HTTP is allowed only on this device's loopback address.")
        }
        return url
    }
}
