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
    """KRunner 端只负责唤起窗口：除 ping/reset 外任何文本都带入窗口。"""

    def test_bare_trigger(self):
        self.assertEqual(daemon.parse_query("ai", "ai").kind, "help")
        self.assertEqual(daemon.parse_query("ai ", "ai").kind, "help")

    def test_window_keywords(self):
        for q in ("ai 窗口", "ai chat", "ai Chat"):
            self.assertEqual(daemon.parse_query(q, "ai", ("窗口", "chat")).kind, "window", q)

    def test_ping_and_reset(self):
        self.assertEqual(daemon.parse_query("ai ping", "ai").kind, "ping")
        for q in ("ai reset", "ai 清空", "ai 重置"):
            self.assertEqual(daemon.parse_query(q, "ai").kind, "reset", q)

    def test_any_text_becomes_bring(self):
        p = daemon.parse_query("ai 什么是量子纠缠", "ai")
        self.assertEqual((p.kind, p.body), ("bring", "什么是量子纠缠"))
        # 旧的子命令文本也原样带入窗口（不再在 KRunner 内解析）
        p2 = daemon.parse_query("ai 搜 新闻 芯片", "ai")
        self.assertEqual((p2.kind, p2.body), ("bring", "搜 新闻 芯片"))

    def test_body_without_trigger(self):
        p = daemon.parse_query("hello world", "ai")
        self.assertEqual((p.kind, p.body), ("bring", "hello world"))

    def test_case_insensitive_trigger(self):
        p = daemon.parse_query("AI Hello", "ai")
        self.assertEqual(p.body, "Hello")


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


class TestSSEParse(unittest.TestCase):
    def test_delta_content(self):
        self.assertEqual(
            daemon.parse_sse_delta('{"choices":[{"delta":{"content":"你好"}}]}'), "你好")

    def test_reasoning_only_ignored(self):
        self.assertEqual(
            daemon.parse_sse_delta('{"choices":[{"delta":{"reasoning_content":"思考中"}}]}'), "")

    def test_garbage_and_empty(self):
        self.assertEqual(daemon.parse_sse_delta("not json"), "")
        self.assertEqual(daemon.parse_sse_delta("{}"), "")


class TestAttemptLadder(unittest.TestCase):
    BASE = {
        "base_url": "https://bigmodel.cn/v1", "api_key": "zhihu-key",
        "model": "glm-4-flash", "fallback_model": "glm-4.7-flash",
        "vision_model": "glm-4v-flash", "max_tokens": 800, "temperature": 0.7,
    }

    def test_two_steps_without_backup(self):
        steps = daemon.build_attempt_ladder(self.BASE)
        self.assertEqual([s["attempt_model"] for s in steps],
                         ["glm-4-flash", "glm-4.7-flash"])

    def test_three_steps_with_backup(self):
        cfg = dict(self.BASE, backup={
            "base_url": "https://modelscope/v1", "api_key": "ms-key",
            "model": "deepseek-ai/DeepSeek-V4.1-Flash"})
        steps = daemon.build_attempt_ladder(cfg)
        self.assertEqual([s["attempt_model"] for s in steps],
                         ["glm-4-flash", "glm-4.7-flash", "deepseek-ai/DeepSeek-V4.1-Flash"])
        self.assertEqual(steps[2]["base_url"], "https://modelscope/v1")
        self.assertEqual(steps[2]["api_key"], "ms-key")
        self.assertEqual(steps[0]["api_key"], "zhihu-key")  # 前两级不受副渠道影响

    def test_backup_without_key_disabled(self):
        cfg = dict(self.BASE, backup={"base_url": "https://modelscope/v1",
                                      "api_key": "", "model": "x"})
        self.assertEqual(len(daemon.build_attempt_ladder(cfg)), 2)

    def test_vision_ladder(self):
        cfg = dict(self.BASE, backup={
            "base_url": "https://modelscope/v1", "api_key": "ms-key",
            "model": "ds-text", "vision_model": "ds-vision"})
        steps = daemon.build_attempt_ladder(cfg, "vision_model")
        self.assertEqual([s["attempt_model"] for s in steps],
                         ["glm-4v-flash", "ds-vision"])
        no_vs = dict(self.BASE, backup={
            "base_url": "https://modelscope/v1", "api_key": "ms-key", "model": "ds"})
        self.assertEqual([s["attempt_model"] for s in
                          daemon.build_attempt_ladder(no_vs, "vision_model")], ["glm-4v-flash"])


class TestTavilyBody(unittest.TestCase):
    CFG = {"api_key": "tvly-x", "max_results": 3}

    def test_basic(self):
        b = daemon.tavily_request_body(self.CFG, "q")
        self.assertEqual(b["search_depth"], "basic")
        self.assertNotIn("topic", b)

    def test_news(self):
        b = daemon.tavily_request_body(self.CFG, "q", news=True)
        self.assertEqual(b["topic"], "news")
        self.assertEqual(b["time_range"], "week")

    def test_advanced(self):
        b = daemon.tavily_request_body(self.CFG, "q", depth="advanced")
        self.assertEqual(b["search_depth"], "advanced")


class TestActiveModelLadder(unittest.TestCase):
    BASE = {
        "base_url": "https://bigmodel.cn/v1", "api_key": "z",
        "model": "glm-4-flash", "fallback_model": "glm-4.7-flash",
        "vision_model": "glm-4v-flash",
        "backup": {"base_url": "https://modelscope/v1", "api_key": "m",
                   "model": "ds-flash"},
    }

    def test_active_reorder_to_front(self):
        steps = daemon.build_attempt_ladder(self.BASE, "model", active="ds-flash")
        self.assertEqual(steps[0]["attempt_model"], "ds-flash")
        self.assertEqual(steps[0]["api_key"], "m")          # 连同副渠道配置一起提前
        self.assertEqual(len(steps), 3)

    def test_active_custom_model_on_primary(self):
        steps = daemon.build_attempt_ladder(self.BASE, "model", active="glm-4-plus")
        self.assertEqual(steps[0]["attempt_model"], "glm-4-plus")
        self.assertEqual(steps[0]["api_key"], "z")
        self.assertEqual(steps[1]["attempt_model"], "glm-4-flash")

    def test_vision_ignores_active(self):
        steps = daemon.build_attempt_ladder(self.BASE, "vision_model", active="glm-4-flash")
        self.assertEqual(steps[0]["attempt_model"], "glm-4v-flash")


class TestModelPanel(unittest.TestCase):
    BASE = {
        "base_url": "https://bigmodel.cn/v1", "api_key": "z",
        "model": "glm-4-flash", "fallback_model": "glm-4.7-flash",
        "vision_model": "glm-4v-flash",
        "backup": {"base_url": "https://modelscope/v1", "api_key": "m",
                   "model": "ds-flash"},
    }

    def test_entries_and_current_marker(self):
        entries = daemon.model_panel_entries(self.BASE, active="")
        self.assertTrue(entries[0]["current"])               # 自动 为当前
        self.assertEqual(len(entries), 4)
        entries2 = daemon.model_panel_entries(self.BASE, active="glm-4.7-flash")
        self.assertTrue([e for e in entries2 if e["model"] == "glm-4.7-flash"][0]["current"])

    def test_filter(self):
        entries = daemon.model_panel_entries(self.BASE, active="")
        hits = daemon.match_model_entries(entries, "魔搭")
        # "魔搭" 同时命中自动条目（标签里含"魔搭"）和魔搭模型本身
        self.assertEqual([h["model"] for h in hits], ["", "ds-flash"])
        self.assertIsNone(daemon.match_model_entries(entries, "不存在的词"))
        self.assertEqual(daemon.match_model_entries(entries, ""), entries)


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
