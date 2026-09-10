import SwiftUI
import WebKit

struct EngineWebView: UIViewRepresentable {
    let url: String
    let token: String

    func makeCoordinator() -> Coordinator {
        Coordinator()
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = true
        let view = WKWebView(frame: .zero, configuration: config)
        view.scrollView.contentInsetAdjustmentBehavior = .never
        context.coordinator.webView = view
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {
        guard let target = Pairing.webURL(engine: url, token: token) else { return }
        if view.url?.absoluteString != target.absoluteString {
            view.load(URLRequest(url: target))
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        weak var webView: WKWebView?
    }
}
