import SwiftUI

@main
struct MailkitApp: App {
    @StateObject private var session = EngineSession()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(session)
                .onOpenURL { url in
                    session.apply(url: url)
                }
        }
    }
}

struct ContentView: View {
    @EnvironmentObject private var session: EngineSession

    var body: some View {
        if session.isConnected {
            EngineWebView(url: session.engineURL, token: session.token)
                .ignoresSafeArea()
        } else {
            ConnectView()
        }
    }
}
