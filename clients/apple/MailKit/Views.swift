import SwiftUI
import WebKit

private let mailAccent = Color(red: 0.55, green: 0.19, blue: 0.28)

struct MailRootView: View {
    @Environment(MailStore.self) private var store
    @Environment(\.colorScheme) private var colorScheme
    @State private var addingAccount = false
    @State private var showingSettings = false
    @State private var draft: ComposeDraft?

    var body: some View {
        Group {
            if store.isConnected {
                NavigationSplitView {
                    List(selection: Binding(get: { store.selectedFolder?.id }, set: { id in
                        if let folder = store.mailboxes.first(where: { $0.id == id }) {
                            store.perform { try await store.selectFolder(folder) }
                        }
                    })) {
                        Section("Accounts") {
                            ForEach(store.accounts) { account in
                                Button {
                                    store.perform { try await store.selectAccount(account) }
                                } label: {
                                    HStack {
                                        VStack(alignment: .leading) {
                                            Text(account.name).foregroundStyle(.primary)
                                            Text(account.address).font(.caption).foregroundStyle(.secondary)
                                        }
                                        Spacer()
                                        if store.selectedAccount?.id == account.id {
                                            Image(systemName: "checkmark").accessibilityLabel("Selected")
                                        }
                                    }
                                }.disabled(store.busy)
                            }
                            Button("Add account", systemImage: "plus") { addingAccount = true }
                        }
                        if store.selectedAccount != nil {
                            Section("Mailboxes") {
                                ForEach(store.mailboxes.filter(\.selectable)) { folder in
                                    NavigationLink(value: folder.id) {
                                        HStack {
                                            Label(folder.name, systemImage: folderIcon(folder.role))
                                            Spacer()
                                            if let unseen = folder.unseen, unseen > 0 {
                                                Text(unseen.formatted()).font(.caption).foregroundStyle(.secondary)
                                            }
                                        }
                                    }.disabled(store.busy)
                                }
                            }
                        }
                    }
                    .navigationTitle("MailKit")
                    .toolbar {
                        ToolbarItem {
                            Button("Settings", systemImage: "gearshape") { showingSettings = true }
                        }
                    }
                    .refreshable { await refreshAccounts() }
                } content: {
                    MessageListView(draft: $draft)
                } detail: {
                    if let message = store.selectedMessage {
                        MessageDetailView(message: message, draft: $draft)
                    } else {
                        ContentUnavailableView("Select a message", systemImage: "envelope.open", description: Text("Choose a mailbox to read, organize, or reply to mail."))
                    }
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    if store.busy {
                        HStack {
                            ProgressView().controlSize(.small)
                            Text("Updating mail…").font(.caption).foregroundStyle(.secondary)
                            Spacer()
                        }.padding(12).background(.regularMaterial)
                    }
                    if let error = store.error {
                        HStack {
                            Image(systemName: "exclamationmark.triangle")
                            Text(error).font(.caption).textSelection(.enabled)
                            Spacer()
                            Button("Dismiss", systemImage: "xmark") { store.error = nil }.labelStyle(.iconOnly)
                        }
                        .padding(12)
                        .background(.regularMaterial)
                        .accessibilityElement(children: .contain)
                    }
                }
            } else {
                ConnectionView()
            }
        }
        .tint(colorScheme == .dark ? Color(red: 0.88, green: 0.73, blue: 0.39) : mailAccent)
        .sheet(isPresented: $addingAccount) { AddAccountView() }
        .sheet(isPresented: $showingSettings) { MailSettingsView() }
        .sheet(item: $draft) { ComposeView(draft: $0) }
    }

    private func refreshAccounts() async {
        do { try await store.loadAccounts() } catch { store.error = error.localizedDescription }
    }
}

private struct ConnectionView: View {
    @Environment(MailStore.self) private var store
    @State private var url = ""
    @State private var token = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    VStack(alignment: .leading, spacing: 12) {
                        Image(systemName: "envelope.badge.shield.half.filled").font(.largeTitle).foregroundStyle(mailAccent)
                        Text("Your mail. One place.").font(.title2.bold())
                        Text("Connect to your MailKit engine to manage your accounts from this device.")
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 12)
                }
                Section {
                    TextField("https://mail.example.com", text: $url).mailInput()
                        .accessibilityLabel("MailKit engine HTTPS address")
                    SecureField("API bearer token", text: $token).mailInput()
                } header: {
                    Text("Engine connection")
                } footer: {
                    Text("The engine must be running and reachable from this device over HTTPS. Your API token is stored in this device’s Keychain. Email credentials are kept by your engine.")
                }
                if let error = store.error { Section { Text(error).foregroundStyle(.red).textSelection(.enabled) } }
                Section {
                    Button {
                        store.perform { try await store.connect(url: url, token: token) }
                    } label: {
                        HStack {
                            Text("Connect")
                            if store.busy { Spacer(); ProgressView() }
                        }
                    }
                    .disabled(store.busy || url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || token.isEmpty)
                }
            }
            .formStyle(.grouped)
            .navigationTitle("Welcome to MailKit")
            .onAppear { url = store.engineURL; token = store.apiToken }
        }
        .frame(minWidth: 320)
    }
}

private struct MessageListView: View {
    @Environment(MailStore.self) private var store
    @Binding var draft: ComposeDraft?

    var body: some View {
        @Bindable var store = store
        List(selection: Binding(get: { store.selectedMessage?.id }, set: { id in
            if let message = store.messages.first(where: { $0.id == id }) {
                store.perform { try await store.openMessage(message) }
            }
        })) {
            ForEach(store.messages) { message in
                NavigationLink(value: message.id) {
                    HStack(alignment: .top, spacing: 10) {
                        Circle().fill(message.unread ? Color.accentColor : .clear).frame(width: 7, height: 7).padding(.top, 7)
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(alignment: .firstTextBaseline) {
                                Text(message.from.map(\.display).joined(separator: ", ")).fontWeight(message.unread ? .semibold : .regular).lineLimit(1)
                                Spacer(minLength: 4)
                                if message.flagged { Image(systemName: "flag.fill").font(.caption).foregroundStyle(.orange).accessibilityLabel("Flagged") }
                                if message.hasAttachments { Image(systemName: "paperclip").font(.caption).accessibilityLabel("Has attachments") }
                            }
                            Text(message.subject.isEmpty ? "(No subject)" : message.subject).font(.subheadline).lineLimit(1)
                            Text(message.snippet).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            if !message.date.isEmpty { Text(message.date).font(.caption2).foregroundStyle(.secondary).lineLimit(1) }
                        }
                    }
                    .padding(.vertical, 4)
                    .accessibilityLabel("\(message.unread ? "Unread. " : "")\(message.from.map(\.display).joined(separator: ", ")). \(message.subject). \(message.snippet)")
                }
                .swipeActions(edge: .trailing) {
                    Button(message.flagged ? "Unflag" : "Flag", systemImage: message.flagged ? "flag.slash" : "flag") {
                        store.perform { try await store.mutateMessage(message, action: message.flagged ? "unflag" : "flag") }
                    }.tint(.orange)
                }
                .swipeActions(edge: .leading) {
                    Button(message.unread ? "Mark read" : "Mark unread", systemImage: message.unread ? "envelope.open" : "envelope.badge") {
                        store.perform { try await store.mutateMessage(message, action: message.unread ? "read" : "unread") }
                    }.tint(.blue)
                }
                .disabled(store.busy)
            }
            if !store.messages.isEmpty {
                Text("Showing up to 100 recent messages. Search this mailbox to find older mail.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .overlay {
            if store.messages.isEmpty {
                if store.busy { ProgressView("Loading mail…") }
                else { ContentUnavailableView(store.selectedFolder == nil ? "Choose a mailbox" : "No messages", systemImage: "tray", description: Text(store.selectedFolder == nil ? "Add or select an account to see its mailboxes." : "Refresh to check for mail, or change your search.")) }
            }
        }
        .navigationTitle(store.selectedFolder?.name ?? "Messages")
        .searchable(text: $store.searchText, prompt: "Search this mailbox")
        .onSubmit(of: .search) { store.perform { try await store.loadMessages() } }
        .onChange(of: store.searchText) { _, value in
            if value.isEmpty, store.selectedFolder != nil { store.perform { try await store.loadMessages() } }
        }
        .refreshable {
            do { try await store.loadMessages() } catch { store.error = error.localizedDescription }
        }
        .toolbar {
            ToolbarItem {
                Button("Refresh", systemImage: "arrow.clockwise") { store.perform { try await store.loadMessages() } }
                    .disabled(store.busy || store.selectedFolder == nil)
            }
            ToolbarItem(placement: .primaryAction) {
                Button("Compose", systemImage: "square.and.pencil") {
                    draft = ComposeDraft(accountID: store.selectedAccount?.id ?? "")
                }.disabled(store.accounts.isEmpty)
            }
        }
        .disabled(store.busy)
    }
}

private struct MessageDetailView: View {
    @Environment(MailStore.self) private var store
    let message: MailMessage
    @Binding var draft: ComposeDraft?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                Text(message.subject.isEmpty ? "(No subject)" : message.subject).font(.title2.bold())
                if let account = store.accounts.first(where: { $0.id == message.accountID }) {
                    Label(account.address, systemImage: "tray").font(.caption).foregroundStyle(.secondary)
                        .accessibilityLabel("Mail account: \(account.address)")
                }
                Text(message.from.map(\.display).joined(separator: ", ")).font(.subheadline.bold())
                Text("To: \(message.to.map(\.display).joined(separator: ", "))").font(.caption).foregroundStyle(.secondary)
                Text(message.date).font(.caption).foregroundStyle(.secondary)
                if message.hasAttachments {
                    Label("Attachments are available in your provider’s app.", systemImage: "paperclip").font(.caption).foregroundStyle(.secondary)
                }
            }
            .textSelection(.enabled)
            .padding()
            Divider()
            if let text = message.bodyText, !text.isEmpty {
                ScrollView {
                    Text(text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding()
                }
            } else if let html = message.bodyHTML, !html.isEmpty {
                SafeMailHTML(html: html)
            } else {
                ScrollView { Text(message.snippet.isEmpty ? "This message has no readable body." : message.snippet).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding() }
            }
        }
        .navigationTitle("Message")
        .toolbar {
            ToolbarItemGroup {
                Button("Reply", systemImage: "arrowshape.turn.up.left") {
                    let recipients = message.replyTo.isEmpty ? message.from : message.replyTo
                    draft = ComposeDraft(accountID: message.accountID, to: recipients.map(\.address).joined(separator: ", "), subject: message.subject.lowercased().hasPrefix("re:") ? message.subject : "Re: \(message.subject)", replyTo: message.id)
                }
                Menu {
                    Button(message.flagged ? "Unflag" : "Flag", systemImage: message.flagged ? "flag.slash" : "flag") {
                        store.perform { try await store.mutateMessage(message, action: message.flagged ? "unflag" : "flag") }
                    }
                    Button(message.unread ? "Mark as read" : "Mark as unread", systemImage: "envelope") {
                        store.perform { try await store.mutateMessage(message, action: message.unread ? "read" : "unread") }
                    }
                    if let archive = store.mailboxes.first(where: { $0.role == "archives" && $0.selectable }) {
                        Button("Archive", systemImage: "archivebox") {
                            store.perform { try await store.mutateMessage(message, action: "move", mailbox: archive.name) }
                        }.disabled(message.mailbox == archive.name)
                    }
                    Menu("Move to mailbox", systemImage: "folder") {
                        ForEach(store.mailboxes.filter { $0.selectable && $0.name != message.mailbox }) { folder in
                            Button(folder.name) { store.perform { try await store.mutateMessage(message, action: "move", mailbox: folder.name) } }
                        }
                    }
                } label: { Label("Message actions", systemImage: "ellipsis.circle") }
            }
        }
        .disabled(store.busy)
    }
}

private struct ComposeDraft: Identifiable {
    let id = UUID()
    var accountID: String
    var to = ""
    var subject = ""
    var replyTo: String? = nil
}

private struct ComposeView: View {
    @Environment(MailStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    let draft: ComposeDraft
    @State private var accountID: String
    @State private var to: String
    @State private var subject: String
    @State private var text = ""
    @State private var sending = false
    @State private var discard = false
    @State private var error: String?

    init(draft: ComposeDraft) {
        self.draft = draft
        _accountID = State(initialValue: draft.accountID)
        _to = State(initialValue: draft.to)
        _subject = State(initialValue: draft.subject)
    }

    private var changed: Bool { !text.isEmpty || to != draft.to || subject != draft.subject }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("From", selection: $accountID) {
                        ForEach(store.accounts) { Text($0.address).tag($0.id) }
                    }.disabled(draft.replyTo != nil)
                    TextField("To (comma separated)", text: $to).mailInput()
                    TextField("Subject", text: $subject)
                }
                Section("Message") {
                    TextEditor(text: $text).frame(minHeight: 240).accessibilityLabel("Message body")
                }
                if let error { Section { Text(error).foregroundStyle(.red).textSelection(.enabled) } }
            }
            .formStyle(.grouped)
            .navigationTitle(draft.replyTo == nil ? "New message" : "Reply")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { if changed { discard = true } else { dismiss() } }.disabled(sending)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(sending ? "Sending…" : "Send", systemImage: "paperplane") {
                        sending = true
                        error = nil
                        Task {
                            defer { sending = false }
                            do {
                                try await store.send(accountID: accountID, to: to, subject: subject, text: text, replyTo: draft.replyTo)
                                dismiss()
                            } catch { self.error = error.localizedDescription }
                        }
                    }.disabled(sending || accountID.isEmpty || to.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .disabled(sending)
            .confirmationDialog("Discard this message?", isPresented: $discard, titleVisibility: .visible) {
                Button("Discard message", role: .destructive) { dismiss() }
            }
        }
        .interactiveDismissDisabled(changed || sending)
        .frame(minWidth: 320, minHeight: 480)
    }
}

private struct AddAccountView: View {
    @Environment(MailStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var form = AccountForm()
    @State private var serverExpanded = false
    @State private var saving = false
    @State private var error: String?

    private var preset: ProviderPreset { ProviderPreset.all.first(where: { $0.id == form.providerID }) ?? ProviderPreset.all[0] }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Email provider", selection: $form.providerID) {
                        ForEach(ProviderPreset.all) { Text($0.name).tag($0.id) }
                    }
                    .onChange(of: form.providerID) { _, id in
                        if let preset = ProviderPreset.all.first(where: { $0.id == id }) { form.apply(preset) }
                        serverExpanded = id == "other"
                    }
                    TextField("Account name", text: $form.name)
                    TextField("Email address", text: $form.address).mailInput()
                } footer: { Text(preset.note) }
                if preset.requiresOAuth {
                    Section {
                        TextField("OAuth application client ID", text: $form.clientID).mailInput()
                    } header: { Text("Secure sign in") } footer: {
                        Text("Use the client ID configured for your MailKit engine. Sign in opens your provider’s page. MailKit selects the provider’s secure servers automatically; Microsoft personal and business settings follow your email domain.")
                    }
                } else {
                    Section {
                        TextField("Username (defaults to email)", text: $form.username).mailInput()
                        SecureField("App password or mail password", text: $form.password).mailInput()
                    } header: { Text("Authentication") } footer: {
                        Text("Use an app-specific password when your provider requires one. Your engine verifies the connection before adding the account.")
                    }
                }
                if !preset.requiresOAuth {
                    DisclosureGroup("Server settings", isExpanded: $serverExpanded) {
                        TextField("IMAP hostname", text: $form.imapHost).mailInput()
                        TextField("IMAP port", value: $form.imapPort, format: .number.grouping(.never))
                        Toggle("IMAP: STARTTLS instead of implicit TLS", isOn: $form.imapSTARTTLS)
                        TextField("SMTP hostname", text: $form.smtpHost).mailInput()
                        TextField("SMTP port", value: $form.smtpPort, format: .number.grouping(.never))
                        Toggle("SMTP: implicit TLS instead of STARTTLS", isOn: $form.smtpTLS)
                        TextField("SMTP username (optional)", text: $form.smtpUsername).mailInput()
                        SecureField("SMTP password (optional)", text: $form.smtpPassword).mailInput()
                        Text("Leave SMTP credentials blank to use your incoming mail credentials. Both servers require encryption. For a custom domain, enter the exact hosts and ports supplied by your email service.").font(.caption).foregroundStyle(.secondary)
                    }
                }
                if let error { Section { Text(error).foregroundStyle(.red).textSelection(.enabled) } }
            }
            .formStyle(.grouped)
            .navigationTitle("Add account")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    Button(saving ? "Connecting…" : preset.requiresOAuth ? "Sign in" : "Connect") {
                        saving = true
                        error = nil
                        Task {
                            defer { saving = false }
                            do { try await store.addAccount(form); dismiss() }
                            catch { self.error = error.localizedDescription }
                        }
                    }.disabled(saving || form.address.isEmpty || (preset.requiresOAuth ? form.clientID.isEmpty : form.password.isEmpty))
                }
            }
            .disabled(saving)
        }
        .interactiveDismissDisabled(saving)
        .frame(minWidth: 360, minHeight: 560)
    }
}

private struct MailSettingsView: View {
    @Environment(MailStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var removal: MailAccount?
    @State private var disconnecting = false
    @State private var result: String?
    @State private var testing = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Engine") {
                    LabeledContent("Address", value: store.engineURL).textSelection(.enabled)
                    Label(store.engineURL.hasPrefix("https:") ? "Encrypted engine connection" : "Local engine connection", systemImage: "lock.shield").foregroundStyle(.secondary)
                    Button("Disconnect this device", role: .destructive) { disconnecting = true }
                }
                Section("Accounts") {
                    ForEach(store.accounts) { account in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(account.name).font(.headline)
                            Text(account.address).font(.caption).foregroundStyle(.secondary)
                            HStack {
                                Button("Test connection") {
                                    testing = true
                                    Task {
                                        defer { testing = false }
                                        do { result = try await store.testAccount(account) }
                                        catch { result = error.localizedDescription }
                                    }
                                }.disabled(testing)
                                Spacer()
                                Button("Remove", role: .destructive) { removal = account }
                            }.buttonStyle(.borderless)
                        }.padding(.vertical, 4)
                    }
                }
                Section {
                    NavigationLink("Mail routing rules") { MailRulesView() }
                } footer: { Text("Rules run on your engine as messages arrive, including while this app is closed.") }
            }
            .formStyle(.grouped)
            .navigationTitle("Settings")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .confirmationDialog("Remove \(removal?.address ?? "account") from your engine?", isPresented: Binding(get: { removal != nil }, set: { if !$0 { removal = nil } }), titleVisibility: .visible) {
                if let account = removal {
                    Button("Remove account", role: .destructive) {
                        Task {
                            do { try await store.removeAccount(account) }
                            catch { result = error.localizedDescription }
                        }
                    }
                }
            } message: { Text("This removes the saved connection and credentials. It does not delete mail from your provider.") }
            .confirmationDialog("Disconnect this device?", isPresented: $disconnecting, titleVisibility: .visible) {
                Button("Disconnect", role: .destructive) { store.disconnect(); dismiss() }
            } message: { Text("The saved API token is removed from this device. Your engine and accounts continue running.") }
            .alert("Connection", isPresented: Binding(get: { result != nil }, set: { if !$0 { result = nil } })) {
                Button("OK") { result = nil }
            } message: { Text(result ?? "") }
        }
        .frame(minWidth: 360, minHeight: 480)
    }
}

private struct MailRulesView: View {
    @Environment(MailStore.self) private var store
    @State private var rules: [MailRule] = []
    @State private var adding = false
    @State private var removal: MailRule?
    @State private var error: String?

    var body: some View {
        List {
            if let error { Text(error).foregroundStyle(.red) }
            ForEach(rules) { rule in
                HStack {
                    Label(rule.name, systemImage: rule.enabled ? "arrow.triangle.branch" : "pause.circle")
                    Spacer()
                    Button("Delete rule", systemImage: "trash", role: .destructive) { removal = rule }.labelStyle(.iconOnly).buttonStyle(.borderless)
                }
            }
        }
        .overlay { if rules.isEmpty && error == nil { ContentUnavailableView("No routing rules", systemImage: "arrow.triangle.branch", description: Text("Add a rule to tag or move incoming mail by subject.")) } }
        .navigationTitle("Mail routing")
        .toolbar { ToolbarItem(placement: .primaryAction) { Button("Add rule", systemImage: "plus") { adding = true }.disabled(store.accounts.isEmpty) } }
        .task { await reload() }
        .refreshable { await reload() }
        .sheet(isPresented: $adding, onDismiss: { Task { await reload() } }) { AddRuleView() }
        .confirmationDialog("Delete this routing rule?", isPresented: Binding(get: { removal != nil }, set: { if !$0 { removal = nil } }), titleVisibility: .visible) {
            if let rule = removal {
                Button("Delete rule", role: .destructive) {
                    Task {
                        do { try await store.removeRule(rule); await reload() }
                        catch { self.error = error.localizedDescription }
                    }
                }
            }
        }
    }

    private func reload() async {
        do { rules = try await store.loadRules(); error = nil }
        catch { self.error = error.localizedDescription }
    }
}

private struct AddRuleView: View {
    @Environment(MailStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var accountID = ""
    @State private var name = ""
    @State private var subject = ""
    @State private var tag = ""
    @State private var move = ""
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Rule name", text: $name)
                    Picker("Account", selection: $accountID) {
                        ForEach(store.accounts) { Text($0.address).tag($0.id) }
                    }
                    TextField("Subject contains", text: $subject)
                }
                Section {
                    TextField("Add tag (optional)", text: $tag)
                    TextField("Move to mailbox (optional)", text: $move)
                } header: { Text("Actions") } footer: { Text("Choose at least one action. Use the exact destination mailbox name from your account.") }
                if let error { Section { Text(error).foregroundStyle(.red) } }
            }
            .formStyle(.grouped)
            .navigationTitle("New routing rule")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    Button(saving ? "Saving…" : "Save") {
                        saving = true
                        Task {
                            defer { saving = false }
                            do { try await store.addRule(name: name, subject: subject, tag: tag, move: move, accountID: accountID); dismiss() }
                            catch { self.error = error.localizedDescription }
                        }
                    }.disabled(saving || name.isEmpty || subject.isEmpty || accountID.isEmpty || (tag.isEmpty && move.isEmpty))
                }
            }
            .disabled(saving)
            .onAppear { accountID = store.selectedAccount?.id ?? store.accounts.first?.id ?? "" }
        }
        .interactiveDismissDisabled(saving)
        .frame(minWidth: 360, minHeight: 420)
    }
}

private func folderIcon(_ role: String) -> String {
    switch role {
    case "inbox": "tray"
    case "sent": "paperplane"
    case "drafts": "doc"
    case "archives": "archivebox"
    case "trash": "trash"
    case "junk": "exclamationmark.shield"
    case "saved": "flag"
    default: "folder"
    }
}

private extension View {
    @ViewBuilder func mailInput() -> some View {
        #if os(iOS)
        self.textInputAutocapitalization(.never).autocorrectionDisabled()
        #else
        self.autocorrectionDisabled()
        #endif
    }
}

// Email HTML is untrusted: disable scripts, remote content, storage, forms, and embedded navigation.
@MainActor
private struct SafeMailHTML {
    let html: String

    func makeCoordinator() -> Coordinator { Coordinator() }

    private func makeWebView(coordinator: Coordinator) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = coordinator
        assert(coordinator.responds(to: NSSelectorFromString("webView:decidePolicyForNavigationAction:decisionHandler:")), "Email navigation policy must be installed")
        view.isInspectable = false
        return view
    }

    private func update(_ view: WKWebView, coordinator: Coordinator) {
        guard coordinator.loadedHTML != html else { return }
        coordinator.loadedHTML = html
        view.loadHTMLString("""
        <!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
        <style>:root{color-scheme:light dark}body{font:17px -apple-system,BlinkMacSystemFont,sans-serif;overflow-wrap:anywhere;padding:12px}img,table{max-width:100%}pre{white-space:pre-wrap}</style></head><body>\(html)</body></html>
        """, baseURL: nil)
    }

    @MainActor final class Coordinator: NSObject, WKNavigationDelegate {
        var loadedHTML: String?

        func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
            guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
            if navigationAction.navigationType == .linkActivated, ["https", "http", "mailto"].contains(url.scheme?.lowercased() ?? "") {
                #if os(iOS)
                UIApplication.shared.open(url)
                #else
                NSWorkspace.shared.open(url)
                #endif
                decisionHandler(.cancel)
            } else {
                decisionHandler(url.absoluteString == "about:blank" ? .allow : .cancel)
            }
        }
    }
}

#if os(iOS)
extension SafeMailHTML: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView { makeWebView(coordinator: context.coordinator) }
    func updateUIView(_ uiView: WKWebView, context: Context) { update(uiView, coordinator: context.coordinator) }
}
#else
extension SafeMailHTML: NSViewRepresentable {
    func makeNSView(context: Context) -> WKWebView { makeWebView(coordinator: context.coordinator) }
    func updateNSView(_ nsView: WKWebView, context: Context) { update(nsView, coordinator: context.coordinator) }
}
#endif
