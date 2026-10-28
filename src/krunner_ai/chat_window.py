#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""krunner-ai 对话窗口（GTK4）

通过 `ai 窗口` 或 KRunner 匹配条目唤出；未运行时由守护进程启动，已运行时置前。
所有问答经守护服务的 DBus 接口（dev.local.krunnerai /chat）完成，
因此与 KRunner 里的问答共享同一份上下文。支持文字与图片（粘贴/选图）。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading

import dbus
import dbus.mainloop.glib
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gdk, Gtk  # noqa: E402

APP_ID = "dev.local.krunnerai.window"
BUS_NAME = "dev.local.krunnerai"
CHAT_PATH = "/chat"
CHAT_IFACE = "dev.local.krunnerai.chat1"

IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif"}
IMAGE_MAX_BYTES = 4 * 1024 * 1024


def get_chat_iface():
    bus = dbus.SessionBus()
    obj = bus.get_object(BUS_NAME, CHAT_PATH)
    return dbus.Interface(obj, CHAT_IFACE)


class ChatApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.win = None
        self.chat = None
        self.image = None  # (b64, mime, 显示名)
        self.busy = False

    # ------------------------------------------------------------------ 框架

    def do_activate(self):  # noqa: N802（GTK 命名约定）
        if self.win is None:
            self._build()
        # 带当前时间戳置前：Wayland 焦点抢占防护下，裸 present() 可能只闪任务栏
        try:
            self.win.present_with_time(GLib.get_real_time() // 1000)
        except Exception:
            self.win.present()

    def _build(self):
        self.chat = get_chat_iface()

        self.win = Gtk.ApplicationWindow(application=self, title="KRunner AI 对话")
        self.win.set_default_size(500, 600)
        self.win.set_icon_name("dev.local.krunnerai")

        # -- 顶栏 --------------------------------------------------------
        hb = Gtk.HeaderBar()
        self.win.set_titlebar(hb)

        self.search_toggle = Gtk.CheckButton(label="联网")
        self.search_toggle.set_tooltip_text("发送时先联网搜索（需配置 Tavily key）")
        hb.pack_start(self.search_toggle)

        reset_btn = Gtk.Button.new_from_icon_name("edit-clear")
        reset_btn.set_tooltip_text("清空上下文")
        reset_btn.connect("clicked", self.on_reset)
        hb.pack_start(reset_btn)

        paste_btn = Gtk.Button.new_from_icon_name("edit-paste")
        paste_btn.set_tooltip_text("粘贴剪贴板图片")
        paste_btn.connect("clicked", self.on_paste_image)
        hb.pack_end(paste_btn)

        attach_btn = Gtk.Button.new_from_icon_name("mail-attachment")
        attach_btn.set_tooltip_text("选择图片文件")
        attach_btn.connect("clicked", self.on_attach_file)
        hb.pack_end(attach_btn)

        # -- 聊天记录 ------------------------------------------------------
        self.view = Gtk.TextView()
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_editable(False)
        self.view.set_cursor_visible(False)
        self.view.set_top_margin(8)
        self.view.set_bottom_margin(8)
        self.view.set_left_margin(8)
        self.view.set_right_margin(8)
        buf = self.view.get_buffer()
        for name, props in {
            "you": {"weight": 700},
            "ai": {"weight": 700, "foreground": "#1c66c2"},
            "err": {"weight": 700, "foreground": "#c01c28"},
            "meta": {"foreground": "#7a7a7a", "scale": 0.85},
        }.items():
            tag = buf.create_tag(name)
            for k, v in props.items():
                tag.set_property(k, v)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_child(self.view)
        sw.set_vexpand(True)
        self.sw = sw

        # -- 已附图提示 ------------------------------------------------------
        self.chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.chip_box.set_visible(False)
        self.chip_label = Gtk.Label()
        self.chip_label.set_halign(Gtk.Align.START)
        chip_remove = Gtk.Button.new_from_icon_name("window-close")
        chip_remove.set_tooltip_text("移除图片")
        chip_remove.connect("clicked", self.on_remove_image)
        self.chip_box.append(self.chip_label)
        self.chip_box.append(chip_remove)

        # -- 输入区 ----------------------------------------------------------
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("输入问题，回车发送（可先附图）")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", lambda *_: self.on_send())

        send_btn = Gtk.Button(label="发送")
        send_btn.connect("clicked", lambda *_: self.on_send())
        self.send_btn = send_btn

        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        input_box.set_margin_top(6)
        input_box.set_margin_bottom(6)
        input_box.set_margin_start(6)
        input_box.set_margin_end(6)
        input_box.append(self.entry)
        input_box.append(send_btn)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        vbox.append(sw)
        vbox.append(self.chip_box)
        vbox.append(input_box)
        self.win.set_child(vbox)

        self._load_history()
        self.win.connect("close-request", lambda *_: False)

    # -------------------------------------------------------------- 记录渲染

    def _append(self, speaker: str, text: str, tag: str):
        """追加一条记录。tag 必须是已注册样式名: you/ai/err/meta。"""
        buf = self.view.get_buffer()
        end = buf.get_end_iter()
        if buf.get_char_count() > 0:
            buf.insert(end, "\n\n")
        if speaker:
            buf.insert_with_tags_by_name(buf.get_end_iter(), speaker + "\n", tag)
        buf.insert(buf.get_end_iter(), text)
        # 滚到底部
        def scroll():
            va = self.sw.get_vadjustment()
            va.set_value(va.get_upper() - va.get_page_size())
            return False
        GLib.idle_add(scroll)

    def _set_busy(self, busy: bool):
        self.busy = busy
        self.entry.set_sensitive(not busy)
        self.send_btn.set_sensitive(not busy)
        title = self.win.get_title()
        self.win.set_title("KRunner AI 对话" + (" — 思考中…" if busy else ""))

    # ---------------------------------------------------------------- 交互

    def on_send(self):
        if self.busy:
            return
        text = self.entry.get_text().strip()
        if not text and not self.image:
            return
        img = self.image
        shown = text + (f"\n📎 {img[2]}" if img else "")
        self._append("你", shown or "（图片）", tag="you")
        self.entry.set_text("")
        self.on_remove_image()
        self._set_busy(True)

        search = self.search_toggle.get_active()

        def worker():
            try:
                if img:
                    reply = self.chat.AskImage(text or "这张图片里有什么？",
                                               img[0], img[1], search, timeout=60)
                else:
                    reply = self.chat.Ask(text, search, timeout=60)
                GLib.idle_add(self._on_reply, str(reply))
            except Exception as exc:
                GLib.idle_add(self._on_reply, json.dumps(
                    {"ok": False, "error": f"守护服务通信失败：{exc}"}))

        threading.Thread(target=worker, daemon=True).start()

    def _on_reply(self, raw: str):
        self._set_busy(False)
        try:
            data = json.loads(raw)
        except Exception:
            data = {"ok": False, "error": "返回解析失败"}
        if data.get("ok"):
            meta = data.get("model", "")
            if data.get("searched"):
                meta += " · 已联网"
            self._append(f"AI（{meta}）" if meta else "AI", data.get("answer", ""), tag="ai")
        else:
            self._append("AI", "✗ " + data.get("error", "未知错误"), tag="err")
        self.win.present()
        self.entry.grab_focus()
        return False

    def on_reset(self, *_):
        try:
            self.chat.Reset(timeout=10)
        except Exception:
            pass
        buf = self.view.get_buffer()
        buf.set_text("")
        self._append("", "已清空上下文，开始新对话。", tag="meta")
    def on_attach_file(self, *_):
        dlg = Gtk.FileDialog()
        dlg.set_title("选择图片")
        filters = Gio_ListStoreFilters()
        dlg.set_filters(filters)
        dlg.open(self.win, None, self._on_file_chosen)

    def _on_file_chosen(self, dlg, result):
        try:
            file = dlg.open_finish(result)
        except Exception:
            return  # 用户取消
        file.load_contents_async(None, self._on_file_loaded, file)

    def _on_file_loaded(self, file, result, user_data):
        try:
            ok, data, _ = file.load_contents_finish(result)
            if not ok:
                raise RuntimeError("读取失败")
        except Exception as exc:
            self._append("", f"✗ 读图失败：{exc}", tag="err")
            return
        name = file.get_basename()
        ext = os.path.splitext(name)[1].lower()
        mime = IMAGE_EXTS.get(ext, "image/png")
        self._set_image(bytes(data), mime, name)

    def on_paste_image(self, *_):
        clip = self.win.get_display().get_clipboard()
        clip.read_texture_async(None, self._on_texture)

    def _on_texture(self, clip, result):
        try:
            texture = clip.read_texture_finish(result)
        except Exception:
            self._append("", "剪贴板里没有图片。", tag="meta")
            return
        if texture is None:
            self._append("", "剪贴板里没有图片。", tag="meta")
            return
        try:
            gbytes = texture.save_to_png_bytes()
        except Exception:
            self._append("", "✗ 当前 GTK 版本不支持读取剪贴板图像数据", tag="err")
            return
        self._set_image(gbytes.get_data(), "image/png", "剪贴板图片")

    def _set_image(self, data: bytes, mime: str, name: str):
        if len(data) > IMAGE_MAX_BYTES:
            self._append("", f"✗ 图片超过 4MB（{len(data) // 1024}KB），请压缩后重试", tag="err")
            return
        b64 = base64.b64encode(data).decode()
        self.image = (b64, mime, f"{name}（{len(data) // 1024}KB）")
        self.chip_label.set_text(f"📎 已附图：{self.image[2]}")
        self.chip_box.set_visible(True)
        self.entry.grab_focus()

    def on_remove_image(self, *_):
        self.image = None
        self.chip_box.set_visible(False)

    # ------------------------------------------------------------------ 初始

    def _load_history(self):
        try:
            info = json.loads(str(self.chat.Info(timeout=10)))
        except Exception as exc:
            self._append("", f"✗ 无法连接守护服务（{exc}）\n"
                          "请确认: systemctl --user start krunner-ai", tag="err")
            return
        if not info.get("llm_ready"):
            self._append("", "⚠ 尚未配置 API key，对话不可用。", tag="meta")
        if not info.get("search_ready"):
            self._append("", "提示：未配置 Tavily key，「联网」开关暂不可用。", tag="meta")
        try:
            history = json.loads(str(self.chat.History(timeout=10)))
        except Exception:
            history = []
        for msg in history:
            if msg.get("role") == "user":
                self._append("你", msg.get("content", ""), tag="you")
            else:
                self._append("AI", msg.get("content", ""), tag="ai")
        if history:
            self._append("", "—— 以上为本会话已有上下文 ——", tag="meta")
        else:
            self._append("", "输入问题开始对话；可附图（glm-4v 免费视觉模型）。", tag="meta")


def Gio_ListStoreFilters():
    from gi.repository import Gio
    store = Gio.ListStore.new(Gtk.FileFilter)
    f = Gtk.FileFilter()
    f.set_name("图片")
    for mime in ("image/png", "image/jpeg", "image/webp", "image/bmp", "image/gif"):
        f.add_mime_type(mime)
    store.append(f)
    return store


def main() -> int:
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    app = ChatApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
