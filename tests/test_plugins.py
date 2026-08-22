from mailkit.plugins.registry import load_plugins


def test_builtin_plugins_load():
    registry = load_plugins()
    for name in ("imap", "gmail", "graph", "yahoo"):
        assert name in registry.providers
    for name in ("password", "oauth2"):
        assert name in registry.auth
    for name in ("idle", "poll", "gmail_push", "graph_push"):
        assert name in registry.watchers
