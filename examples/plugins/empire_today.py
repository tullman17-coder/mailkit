"""Example message-processing hook. Drop a copy in ~/.mailkit/plugins/.

This tags Empire Today claim mail so agents can subscribe with --rule or --tag
instead of rescanning every inbox.
"""

from mailkit.plugins.types import HookAction, HookContext


class EmpireTodayHook:
    plugin_type = "hook"
    id = "empire_today"
    priority = 200

    def process(self, ctx: HookContext) -> HookAction | None:
        blob = " ".join(
            [
                ctx.message.subject or "",
                ctx.message.snippet or "",
                " ".join(a.address for a in ctx.message.from_),
            ]
        ).lower()
        if "empire today" not in blob and "empiretoday.com" not in blob:
            return None
        if "claim" not in blob:
            return HookAction(tag=["empire-today"])
        return HookAction(tag=["empire-today", "claim"], flag=True)


def register(registry) -> None:
    registry.register(EmpireTodayHook())
