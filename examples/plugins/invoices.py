"""Example message-processing hook. Drop a copy in ~/.mailkit/plugins/.

Tags vendor invoices so agents can subscribe with --tag instead of
rescanning every inbox.
"""

from mailkit.plugins.types import HookAction, HookContext


class InvoiceHook:
    plugin_type = "hook"
    id = "invoices"
    priority = 200

    def process(self, ctx: HookContext) -> HookAction | None:
        blob = " ".join(
            [
                ctx.message.subject or "",
                ctx.message.snippet or "",
                " ".join(a.address for a in ctx.message.from_),
            ]
        ).lower()
        if "invoice" not in blob and "vendor.example" not in blob:
            return None
        return HookAction(tag=["invoice"], flag=True)


def register(registry) -> None:
    registry.register(InvoiceHook())
