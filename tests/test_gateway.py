import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.adapters.tools.gateway import Risk, Tool, ToolGateway
from jarvis.core.errors import (
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolPermissionDenied,
)
from jarvis.core.turn import TurnContext


class _EchoTool(Tool):
    name = "echo"
    input_schema = {"value": {"required": True}}

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> str:
        return str(arguments["value"])


class _FailingTool(Tool):
    name = "failing"
    input_schema = {}

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> str:
        raise ValueError("boom")


class _SlowTool(Tool):
    name = "slow"
    input_schema = {}
    timeout_seconds = 0.01

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> str:
        await asyncio.sleep(0.1)
        return "finished"


class _ConfirmTool(Tool):
    name = "confirm"
    input_schema = {}
    risk = Risk.CONFIRM_REQUIRED

    async def execute(self, arguments: Mapping[str, Any], context: TurnContext) -> str:
        return "executed"


class ToolGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_tool_raises_not_found_error(self):
        gateway = ToolGateway([])

        with self.assertRaises(ToolNotFoundError) as raised:
            await gateway.execute("unknown", {}, TurnContext.fresh("conversation"))

        self.assertIsInstance(raised.exception, ToolError)
        self.assertIn("tool not registered: unknown", str(raised.exception))

    async def test_allowlist_permits_only_listed_tools(self):
        gateway = ToolGateway([_EchoTool()], allowlist=frozenset({"echo"}))
        context = TurnContext.fresh("conversation")

        self.assertEqual("allowed", await gateway.execute("echo", {"value": "allowed"}, context))
        with self.assertRaisesRegex(ToolPermissionDenied, "is not allowlisted"):
            await ToolGateway([_FailingTool()], allowlist=frozenset()).execute(
                "failing", {}, context
            )

    async def test_missing_required_argument_raises_unwrapped_execution_error(self):
        gateway = ToolGateway([_EchoTool()])

        with self.assertRaisesRegex(ToolExecutionError, "missing required arguments") as raised:
            await gateway.execute("echo", {}, TurnContext.fresh("conversation"))

        self.assertNotIn("failed:", str(raised.exception))

    async def test_tool_value_error_is_wrapped(self):
        gateway = ToolGateway([_FailingTool()])

        with self.assertRaisesRegex(ToolExecutionError, "tool failing failed: boom"):
            await gateway.execute("failing", {}, TurnContext.fresh("conversation"))

    async def test_tool_timeout_is_wrapped(self):
        gateway = ToolGateway([_SlowTool()])

        with self.assertRaisesRegex(ToolExecutionError, "tool slow timed out"):
            await gateway.execute("slow", {}, TurnContext.fresh("conversation"))

    async def test_confirmation_acceptance_executes_tool(self):
        confirmations: list[tuple[str, Mapping[str, Any], TurnContext]] = []

        async def approve(name: str, arguments: Mapping[str, Any], context: TurnContext) -> bool:
            confirmations.append((name, arguments, context))
            return True

        gateway = ToolGateway([_ConfirmTool()], confirmer=approve)
        context = TurnContext.fresh("conversation")

        self.assertEqual("executed", await gateway.execute("confirm", {}, context))
        self.assertEqual([("confirm", {}, context)], confirmations)

    async def test_confirmation_decline_denies_tool(self):
        async def decline(name: str, arguments: Mapping[str, Any], context: TurnContext) -> bool:
            return False

        gateway = ToolGateway([_ConfirmTool()], confirmer=decline)

        with self.assertRaisesRegex(ToolPermissionDenied, "declined"):
            await gateway.execute("confirm", {}, TurnContext.fresh("conversation"))

    async def test_confirmation_without_confirmer_denies_tool(self):
        gateway = ToolGateway([_ConfirmTool()])

        with self.assertRaisesRegex(ToolPermissionDenied, "no confirmer"):
            await gateway.execute("confirm", {}, TurnContext.fresh("conversation"))

    async def test_confirmation_approval_expires_for_the_same_trace(self):
        confirmation_calls = 0

        async def approve(name: str, arguments: Mapping[str, Any], context: TurnContext) -> bool:
            nonlocal confirmation_calls
            confirmation_calls += 1
            return True

        gateway = ToolGateway(
            [_ConfirmTool()], confirmer=approve, confirmation_timeout_seconds=0.01
        )
        context = TurnContext.fresh("conversation")

        await gateway.execute("confirm", {}, context)
        await asyncio.sleep(0.02)
        await gateway.execute("confirm", {}, context)

        self.assertEqual(2, confirmation_calls)

    async def test_reversible_property_matches_risk_policy(self):
        tool = _EchoTool()

        for risk in (Risk.READ_ONLY, Risk.REVERSIBLE):
            tool.risk = risk
            self.assertTrue(tool.reversible)
        for risk in (Risk.CONFIRM_REQUIRED, Risk.HIGH_RISK):
            tool.risk = risk
            self.assertFalse(tool.reversible)


if __name__ == "__main__":
    unittest.main()
