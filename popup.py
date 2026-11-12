#!/usr/bin/env python3
"""작업이 끝났을 때 이 PC 화면 위 가운데에 잠깐 뜨는 배너 — board 서버가 부른다. 모양은 web/popup.html.

  python3 popup.py '<JSON>'                     띄운다. JSON = {title, session, now, at, url}
  python3 popup.py '<JSON>' --shot out.png [ms] 화면에 안 띄우고 ms 뒤 모습을 PNG 로(시험용)

GNOME 알림 대신 직접 그리는 이유: 알림은 모양을 못 바꾼다(11-12 사용자 "엄청 화려하게 · 세션마다 달라도 · 특징적이게").
포커스를 뺏지 않는다(쓰던 창의 입력이 안 끊기게). 누르면 그 작업 화면, 9초 뒤 저절로 닫힌다(마우스를 올리면 멈춤).
X11 + 합성 관리자 전제(이 PC GNOME). 웨일랜드는 창 위치를 못 정해 가운데 위에 안 뜰 수 있다.
"""
import json
import pathlib
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.0")
from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402

W, H = 640, 230
HTML = pathlib.Path(__file__).resolve().parent / "web" / "popup.html"


def page(data):
    js = "window.POP = " + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + ";"
    return HTML.read_text(encoding="utf-8").replace("/*DATA*/", js, 1)


def view_for(data, on_msg=None):
    ucm = WebKit2.UserContentManager()
    if on_msg:
        ucm.register_script_message_handler("board")
        ucm.connect("script-message-received::board", lambda _m, res: on_msg(res.get_js_value().to_string()))
    v = WebKit2.WebView.new_with_user_content_manager(ucm)
    v.set_background_color(Gdk.RGBA(0, 0, 0, 0))
    v.load_html(page(data), "file:///")
    return v


def show(data):
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_keep_above(True)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_accept_focus(False)
    win.set_focus_on_map(False)
    win.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
    win.set_app_paintable(True)
    if (vis := win.get_screen().get_rgba_visual()) is not None:
        win.set_visual(vis)
    d = Gdk.Display.get_default()
    g = (d.get_primary_monitor() or d.get_monitor(0)).get_geometry()
    win.set_default_size(W, H)
    win.move(g.x + (g.width - W) // 2, g.y + 6)

    def on_msg(m):
        if m == "open" and data.get("url"):
            subprocess.Popen(["xdg-open", data["url"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        Gtk.main_quit()
    win.add(view_for(data, on_msg))
    win.show_all()
    GLib.timeout_add_seconds(40, Gtk.main_quit)   # 화면이 멈춰도 창이 남지 않게(마우스를 올려 둔 채면 40초에 닫힘)
    Gtk.main()


def shot(data, out, ms):
    import cairo
    win = Gtk.OffscreenWindow()
    v = view_for(data)
    v.set_size_request(W, H)
    win.add(v)
    win.show_all()

    def take():
        def done(view, res):
            surf = view.get_snapshot_finish(res)
            img = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
            c = cairo.Context(img)
            c.set_source_rgb(.12, .12, .12)   # 투명 배경을 어두운 바탕 위에 — 실제 화면과 비슷하게
            c.paint()
            c.set_source_surface(surf, 0, 0)
            c.paint()
            img.write_to_png(out)
            Gtk.main_quit()
        v.get_snapshot(WebKit2.SnapshotRegion.VISIBLE, WebKit2.SnapshotOptions.TRANSPARENT_BACKGROUND, None, done)
        return False
    GLib.timeout_add(ms, take)
    GLib.timeout_add_seconds(20, Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    data = json.loads(sys.argv[1])
    if len(sys.argv) > 3 and sys.argv[2] == "--shot":
        shot(data, sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 2500)
    else:
        show(data)
