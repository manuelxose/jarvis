import unittest
import importlib
import sys
import types
from unittest import mock
from unittest.mock import Mock


class ActionRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stubs = {
            "actions.ais_monitor": "AISMonitor",
            "actions.pc_control": "PCController",
            "actions.trading_monitor": "TradingMonitor",
            "actions.web_search": "WebSearch",
        }
        modules = {}
        for module_name, class_name in stubs.items():
            module = types.ModuleType(module_name)
            setattr(module, class_name, type(class_name, (), {}))
            modules[module_name] = module
        llm_module = types.ModuleType("brain.llm")
        llm_module.OllamaClient = type("OllamaClient", (), {})
        modules["brain.llm"] = llm_module
        with mock.patch.dict(sys.modules, modules):
            cls.ActionRouter = importlib.import_module("brain.action_router").ActionRouter

    def test_conversation_does_not_make_a_second_llm_classification_call(self):
        llm = Mock()
        router = self.ActionRouter(llm_client=llm)

        self.assertEqual("CONVERSATION", router.classify_intent("que tiempo hace hoy"))
        llm.chat.assert_not_called()

    def test_explicit_action_keywords_still_route_without_llm(self):
        llm = Mock()
        router = self.ActionRouter(llm_client=llm)

        self.assertEqual("WEB_SEARCH", router.classify_intent("busca en internet noticias de hoy"))
        llm.chat.assert_not_called()
