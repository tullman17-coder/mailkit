import SwiftUI

struct ConnectView: View {
    @EnvironmentObject private var session: EngineSession
    @State private var url: String = ""
    @State private var token: String = ""
    @State private var error: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Mailkit is a native app that talks to the engine on your computer. Paste the pair URL from `mailkit pair --lan`, or open the mailkit:// link.")
                        .font(.footnote)
                }
                Section("Engine") {
                    TextField("http://192.168.1.10:8765", text: $url)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    SecureField("Token from mailkit pair", text: $token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                Section {
                    Button("Connect") {
                        if url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            error = "Engine URL and token are required."
                            return
                        }
                        error = ""
                        session.connect(url: url, token: token)
                    }
                }
                if !error.isEmpty {
                    Section {
                        Text(error).foregroundStyle(.red)
                    }
                }
                Section("Agents") {
                    Text("AI agents use the same engine via the CLI: mailkit -o json messages list --mailbox inbox")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Mailkit")
            .onAppear {
                url = session.engineURL
                token = session.token
            }
        }
    }
}
