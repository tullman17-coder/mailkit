import SwiftUI

@main
struct MailKitApp: App {
    @State private var store = MailStore()
    var body: some Scene {
        WindowGroup {
            MailRootView()
                .environment(store)
                .tint(Color(red: 0.60, green: 0.39, blue: 0.12))
        }
    }
}
