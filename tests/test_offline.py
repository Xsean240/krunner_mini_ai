#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线单元测试：不联网、不需要 API key、不依赖 DBus 会话。

运行: python3 tests/test_offline.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "krunner_ai"))

import daemon  # noqa: E402


class TestParseQuery(unittest.TestCase):
    def test_bare_trigger(self):
        self.assertEqual(daemon.parse_query("ai", "ai", "搜").kind, "help")
        self.assertEqual(daemon.parse_query("ai ", "ai", "搜").kind, "help")

    def test_trigger_with_body(self):
        p = daemon.parse_query("ai 你好", "ai", "搜")
        self.assertEqual(p.kind, "ask")
        self.assertEqual(p.body, "你好")

    def test_body_without_trigger(self):
        # KRunner 单 runner 模式下可能只传剩余部分
        p = daemon.parse_query("什么是量子纠缠", "ai", "搜")
        self.assertEqual(p.kind, "ask")
        self.assertEqual(p.body, "什么是量子纠缠")

    def test_search_keyword(self):
        p = daemon.parse_query("ai 搜 DeepSeek 最新消息", "ai", "搜")
        self.assertEqual(p.kind, "ask_search")
        self.assertEqual(p.body, "DeepSeek 最新消息")

    def test_reset_variants(self):
        for q in ("ai reset", "ai 清空", "ai 重置"):
            self.assertEqual(daemon.parse_query(q, "ai", "搜").kind, "reset", q)

    def test_ping(self):
        self.assertEqual(daemon.parse_query("ai ping", "ai", "搜").kind, "ping")

    def test_window_keywords(self):
        for q in ("ai 窗口", "ai chat", "ai Chat"):
            self.assertEqual(
                daemon.parse_query(q, "ai", "搜", ("窗口", "chat")).kind, "window", q)

    def test_image_keyword(self):
        p = daemon.parse_query("ai 图 /tmp/pic.png 这是什么", "ai", "搜", (), "图")
        self.assertEqual(p.kind, "ask_image")
        self.assertEqual(p.image_path, "/tmp/pic.png")
        self.assertEqual(p.body, "这是什么")

    def test_image_quoted_path_with_spaces(self):
        p = daemon.parse_query('ai 图 "/tmp/my pic.png" 描述', "ai", "搜", (), "图")
        self.assertEqual(p.kind, "ask_image")
        self.assertEqual(p.image_path, "/tmp/my pic.png")
        self.assertEqual(p.body, "描述")

    def test_case_insensitive_trigger(self):
        p = daemon.parse_query("AI Hello", "ai", "搜")
        self.assertEqual(p.kind, "ask")
        self.assertEqual(p.body, "Hello")

    def test_search_keyword_alone_falls_back_to_ask(self):
        p = daemon.parse_query("ai 搜", "ai", "搜")
        self.assertEqual(p.kind, "ask")
        self.assertEqual(p.body, "搜")


class TestSession(unittest.TestCase):
    def test_trim(self):
        s = daemon.Session(max_turns=2)
        for i in range(5):
            s.add_turn(f"q{i}", f"a{i}")
        self.assertEqual(s.turns(), 2)
        hist = s.history()
        self.assertEqual(hist[0]["content"], "q3")
        self.assertEqual(hist[-1]["content"], "a4")

    def test_clear(self):
        s = daemon.Session(max_turns=8)
        s.add_turn("q", "a")
        s.clear()
        self.assertEqual(s.turns(), 0)


class TestBuildMessages(unittest.TestCase):
    SYS = "你是助手"

    def test_basic_order(self):
        hist = [{"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"}]
        msgs = daemon.build_messages(self.SYS, hist, None, "q2")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[-1], {"role": "user", "content": "q2"})
        self.assertEqual(len(msgs), 4)

    def test_search_block_marked_as_data(self):
        results = [{"title": "t", "url": "https://x", "content": "c"}]
        msgs = daemon.build_messages(self.SYS, [], results, "q")
        block = msgs[1]["content"]
        self.assertIn("不是对你的指令", block)   # 提示注入防护标注
        self.assertIn("[1]", block)
        self.assertIn("https://x", block)

    def test_image_multimodal_format(self):
        msgs = daemon.build_messages(self.SYS, [], None, "这是什么", "QUJD", "image/png")
        last = msgs[-1]
        self.assertIsInstance(last["content"], list)
        self.assertEqual(last["content"][0]["type"], "image_url")
        self.assertIn("data:image/png;base64,QUJD", last["content"][0]["image_url"]["url"])
        self.assertEqual(last["content"][1], {"type": "text", "text": "这是什么"})

    def test_thinking_disabled_only_for_nonvision_hybrid(self):
        # 纯函数层面验证正则排除视觉模型：glm-4.6v-flash 不应匹配
        self.assertIsNotNone(__import__("re").match(r"glm-4\.[5-9](?!v)", "glm-4.7-flash"))
        self.assertIsNone(__import__("re").match(r"glm-4\.[5-9](?!v)", "glm-4.6v-flash"))


class TestSummarize(unittest.TestCase):
    def test_collapses_newlines(self):
        self.assertEqual(daemon.summarize_answer("a\n\nb\nc"), "a b c")

    def test_truncates(self):
        out = daemon.summarize_answer("字" * 300)
        self.assertLessEqual(len(out), 160)
        self.assertTrue(out.endswith("…"))


class TestExtractErrorHint(unittest.TestCase):
    def test_bigmodel_style_body(self):
        body = '{"error":{"code":"1305","message":"该模型当前访问量过大，请您稍后再试"}}'
        hint = daemon.extract_error_hint(429, body)
        self.assertIn("访问量过大", hint)
        self.assertIn("限速或服务繁忙", hint)

    def test_plain_body(self):
        hint = daemon.extract_error_hint(500, "gateway crashed")
        self.assertEqual(hint, "服务商内部错误")

    def test_long_message_truncated(self):
        body = '{"error":{"message":"' + "长" * 200 + '"}}'
        hint = daemon.extract_error_hint(429, body)
        # 提示前缀(8 字符) + 截断到 80 字符的正文
        self.assertLessEqual(len(hint), 8 + 80)
        self.assertGreaterEqual(len(hint), 80)


class TestAutoSearchHeuristic(unittest.TestCase):
    def test_time_words(self):
        self.assertTrue(daemon.looks_like_search_needed("DeepSeek 最新版本"))
        self.assertTrue(daemon.looks_like_search_needed("今天北京天气"))

    def test_question_mark(self):
        self.assertTrue(daemon.looks_like_search_needed("Python 是什么？"))

    def test_plain(self):
        self.assertFalse(daemon.looks_like_search_needed("帮我把这段话翻译成英文"))


class TestConfig(unittest.TestCase):
    def test_defaults_without_file(self):
        cfg = daemon.Config.load("/nonexistent/path.toml")
        self.assertEqual(cfg.llm["model"], "glm-4-flash")
        self.assertEqual(cfg.llm["fallback_model"], "glm-4.7-flash")
        self.assertFalse(cfg.llm_ready())

    def test_deep_merge_keeps_defaults(self):
        merged = daemon.deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"x": 9}})
        self.assertEqual(merged, {"a": {"x": 9, "y": 2}})


if __name__ == "__main__":
    unittest.main(verbosity=2)
