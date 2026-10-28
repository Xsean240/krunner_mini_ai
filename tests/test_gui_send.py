#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 驱动测试：启动真实窗口、模拟发送一条消息、验证回答渲染出来。"""
import importlib.util
from importlib.machinery import SourceFileLoader
import os
import sys

import dbus  # noqa: F401
import dbus.mainloop.glib
from gi.repository import GLib

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

_path = os.path.expanduser("~/.local/bin/krunner-ai-chat")
_loader = SourceFileLoader("cw", _path)
spec = importlib.util.spec_from_loader("cw", _loader)
cw = importlib.util.module_from_spec(spec)
_loader.exec_module(cw)

app = cw.ChatApp()
RESULT = {"ok": False, "detail": ""}


def step():
    """窗口就绪后：填入文字并触发发送。"""
    try:
        app.entry.set_text("驱动测试：只回复ok")
        app.on_send()
        RESULT["sent"] = True
    except Exception as exc:
        RESULT["detail"] = f"发送异常: {exc}"
        app.quit()
    return False


def check():
    buf = app.view.get_buffer()
    text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
    ok = ("ok" in text.lower()) and ("你" in text) and ("AI" in text)
    RESULT["ok"] = ok
    RESULT["detail"] = text[-260:]
    app.quit()
    return False


GLib.timeout_add(1500, step)
GLib.timeout_add(12000, check)
app.run([])

print("GUI_SEND_TEST:", "PASS ✓" if RESULT["ok"] else "FAIL ✗")
print("日志尾部:", RESULT["detail"].replace("\n", " | ")[:240])
sys.exit(0 if RESULT["ok"] else 1)
