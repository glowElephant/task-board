#!/usr/bin/env python3
"""board — 확인판. 여러 Claude 세션이 사용자에게 물을 것을 작업별 문서(탭 하나)로 모은다.

파일이 원본이다: $BOARD_DIR/<작업>/task.md (+ img/). 서버는 그 파일을 보여 주고 사용자의 답·메모·이미지를
그 자리에 고쳐 넣을 뿐 상태를 따로 갖지 않는다. 사용자가 [보내기]를 누르면 <작업>/.sent 가 바뀌고,
그 작업을 `board wait` 로 기다리던 세션이 깨어난다.

  board serve                               화면 서버 (127.0.0.1:BOARD_PORT)
  board new <작업> --title T --session S    작업 문서 만들기
  board wait <작업>... [--max 분]           [보내기]까지 대기 — 세션은 Bash run_in_background 로 건다
  board done <작업>                         끝으로 표시 (목록 '끝난 작업' 으로)
  board list                                작업 목록
  board url [<작업>]                        화면 주소

설정: 환경변수 > ~/.config/board/env (KEY=VALUE). BOARD_DIR(기본 ~/board) · BOARD_PORT(기본 8770) ·
BOARD_EXTRA(옛 txt 문서 폴더, os.pathsep 로 여럿 — 탭으로 보여 주기만 한다).
"""
import argparse
import datetime
import http.server
import json
import mimetypes
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import urllib.parse

HOME = pathlib.Path.home()


def _config():
    cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "board" / "env"
    vals = {}
    if cfg.is_file():
        for ln in cfg.read_text(encoding="utf-8").splitlines():
            k, sep, v = ln.partition("=")
            if sep and not k.strip().startswith("#"):
                vals[k.strip()] = v.strip()
    vals.update({k: v for k, v in os.environ.items() if k.startswith("BOARD_")})
    return vals


CFG = _config()
BOARD = pathlib.Path(CFG.get("BOARD_DIR") or HOME / "board").expanduser()
PORT = int(CFG.get("BOARD_PORT") or 8770)
EXTRA = [pathlib.Path(p).expanduser() for p in (CFG.get("BOARD_EXTRA") or "").split(os.pathsep) if p]
NOTIFY = (CFG.get("BOARD_NOTIFY") or "1") != "0"
WEB = pathlib.Path(__file__).resolve().parent / "web"

FM = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
ITEM = re.compile(r"^###\s*\[([ ~vV])\]\s*([^\s.]+)\.?\s*(.*)$")
HEAD = re.compile(r"^#{1,3}\s")
DONE = {"끝", "done"}
MEMO = "메모"


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


# ── 문서 해석 ────────────────────────────────────────────────────────────────

def fences(lines):
    """줄마다 코드 펜스 안인지 — 펜스 안의 `# 주석`·`->` 를 제목·답으로 읽지 않으려고."""
    inside, out = False, []
    for ln in lines:
        if ln.lstrip().startswith("```"):
            out.append(True)
            inside = not inside
        else:
            out.append(inside)
    return out


def split(text):
    m = FM.match(text)
    meta = {}
    if m:
        for ln in m.group(1).splitlines():
            k, sep, v = ln.partition(":")
            if sep:
                meta[k.strip()] = v.strip()
    return meta, (text[m.end():] if m else text)


def scan(lines):
    """질문 하나의 줄 → (설명 줄, 대화). `->` 는 사용자, `⇒` 는 세션. 붙어 있는 다음 줄은 같은 말."""
    desc, ents = [], []
    for i, (ln, fenced) in enumerate(zip(lines, fences(lines))):
        s = ln.strip()
        who = None if fenced else "me" if s.startswith("->") else "claude" if s.startswith("⇒") else None
        last = ents[-1] if ents else None
        glued = last is not None and last["b"] == i
        if who:
            t = s[2:] if who == "me" else s[1:]
            t = t[1:] if t[:1] == " " else t
            if glued and last["who"] == who:
                last["text"] += "\n" + t
                last["b"] = i + 1
            else:
                ents.append({"who": who, "text": t, "a": i, "b": i + 1})
        elif glued and s:
            last["text"] += "\n" + s
            last["b"] = i + 1
        else:
            desc.append(ln)
    return desc, ents


def turn(status, ents):
    if status == "v":
        return "done"
    return "claude" if ents and ents[-1]["who"] == "me" else "me"


def parse(body):
    """본문 → 블록 목록: md / item(### [ ] Q1. 제목 ~ 다음 제목) / memo(## 메모 아래)."""
    lines = body.split("\n")
    out, cur = [], None
    for ln, fenced in zip(lines, fences(lines)):
        m = None if fenced else ITEM.match(ln)
        if m:
            cur = {"type": "item", "status": m.group(1).lower(), "id": m.group(2), "title": m.group(3), "lines": []}
            out.append(cur)
            continue
        if not fenced and HEAD.match(ln):
            cur = None
            if ln.lstrip("#").strip() == MEMO:
                out.append({"type": "md", "lines": [ln]})
                cur = {"type": "memo", "lines": []}
                out.append(cur)
                continue
        if cur is None:
            if not out or out[-1]["type"] != "md":
                out.append({"type": "md", "lines": []})
            out[-1]["lines"].append(ln)
        else:
            cur["lines"].append(ln)
    for b in out:
        lines = b.pop("lines")
        if b["type"] == "item":
            desc, ents = scan(lines)
            b["desc"] = "\n".join(desc).strip()
            b["entries"] = [{"who": e["who"], "text": e["text"]} for e in ents]
            b["turn"] = turn(b["status"], ents)
        else:
            b["text"] = "\n".join(lines).strip("\n")
    return out


def _item_range(lines, item_id):
    fz = fences(lines)
    start = next(i for i, l in enumerate(lines) if not fz[i] and (m := ITEM.match(l)) and m.group(2) == item_id)
    end = next((i for i in range(start + 1, len(lines)) if not fz[i] and HEAD.match(lines[i])), len(lines))
    return start, end


def set_answer(text, item_id, answer):
    """질문의 내 최신 답을 answer 로 — 마지막 말이 내 것이면 바꾸고, 아니면 끝에 새로 붙인다."""
    lines = text.split("\n")
    start, end = _item_range(lines, item_id)
    _, ents = scan(lines[start + 1:end])
    new = [("-> " + l) if l else "->" for l in answer.rstrip().split("\n")] if answer.strip() else []
    if ents and ents[-1]["who"] == "me":
        lines[start + 1 + ents[-1]["a"]:start + 1 + ents[-1]["b"]] = new
    elif new:
        k = end
        while k > start + 1 and not lines[k - 1].strip():
            k -= 1
        lines[k:k] = new
    return "\n".join(lines)


def set_memo(text, memo):
    lines = text.split("\n")
    fz = fences(lines)
    body = memo.rstrip().split("\n") if memo.strip() else []
    i = next((i for i, l in enumerate(lines) if not fz[i] and HEAD.match(l) and l.lstrip("#").strip() == MEMO), None)
    if i is None:
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines + ["", "## " + MEMO] + body + [""])
    end = next((j for j in range(i + 1, len(lines)) if not fz[j] and HEAD.match(lines[j])), len(lines))
    lines[i + 1:end] = body + [""]
    return "\n".join(lines)


def set_meta(text, key, value):
    m = FM.match(text)
    if not m:
        return f"---\n{key}: {value}\n---\n" + text
    rows = [l for l in m.group(1).splitlines() if l.partition(":")[0].strip() != key] + [f"{key}: {value}"]
    return "---\n" + "\n".join(rows) + "\n---\n" + text[m.end():]


# ── 파일 ─────────────────────────────────────────────────────────────────────

def write(path, text):
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def resolve(tid):
    """작업 id → (문서 파일, 작업 폴더). 옛 txt 는 'txt:<파일명>', 폴더 없음."""
    if tid.startswith("txt:"):
        name = tid[4:]
        if name and "/" not in name and not name.startswith("."):
            for d in EXTRA:
                f = d / name
                if f.suffix == ".txt" and f.is_file():
                    return f, None
        raise KeyError(tid)
    if not tid or "/" in tid or tid.startswith("."):
        raise KeyError(tid)
    d = BOARD / tid
    if not (d / "task.md").is_file():
        raise KeyError(tid)
    return d / "task.md", d


def summary(tid):
    f, d = resolve(tid)
    text = f.read_text(encoding="utf-8", errors="replace")
    if d is None:
        mine = len(re.findall(r"^\s*→ 사용자[^:\n]*:\s*$", text, re.M))
        recent = time.time() - f.stat().st_mtime < 2 * 86400   # 옛 txt 는 끝 표시가 없다 — 이틀 손 안 대면 끝으로 본다
        return {"id": tid, "kind": "txt", "title": text.split("\n", 1)[0].strip()[:80] or f.name,
                "session": "txt 문서 · " + str(f.parent).replace(str(HOME), "~"),
                "status": "진행 중" if mine or re.search(r"^\s*\[[ ~]\]", text, re.M) or recent else "끝",
                "mine": mine, "waiting": 0, "mtime": f.stat().st_mtime}
    meta, body = split(text)
    items = [b for b in parse(body) if b["type"] == "item"]
    return {"id": tid, "kind": "task", "title": meta.get("title") or tid, "session": meta.get("session", ""),
            "status": "끝" if meta.get("status", "") in DONE else "진행 중",
            "mine": sum(i["turn"] == "me" for i in items), "waiting": sum(i["turn"] == "claude" for i in items),
            "mtime": f.stat().st_mtime}


def all_ids():
    ids = [p.name for p in sorted(BOARD.iterdir()) if (p / "task.md").is_file()] if BOARD.is_dir() else []
    seen = set()
    for d in EXTRA:
        for f in sorted(d.glob("*.txt")) if d.is_dir() else []:
            if f.name not in seen:
                seen.add(f.name)
                ids.append("txt:" + f.name)
    return ids


def detail(tid):
    f, d = resolve(tid)
    text = f.read_text(encoding="utf-8", errors="replace")
    out = summary(tid) | {"raw": text}
    if d is not None:
        meta, body = split(text)
        out["blocks"] = parse(body)
    return out


def send(d, items):
    (d / ".sent").write_text(json.dumps({"at": now(), "items": items}, ensure_ascii=False), encoding="utf-8")


# ── 서버 ─────────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body, ctype="application/json; charset=utf-8", code=200):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path in ("/", "/index.html"):
                return self.reply((WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/tasks":
                return self.reply([summary(t) for t in all_ids()])
            if u.path == "/api/task":
                return self.reply(detail(q["id"]))
            if u.path.startswith("/files/"):
                tid, _, rel = urllib.parse.unquote(u.path[len("/files/"):]).partition("/")
                _, d = resolve(tid)
                f = (d / rel).resolve()
                if d.resolve() in f.parents and f.is_file():
                    return self.reply(f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        except (KeyError, StopIteration, TypeError, AttributeError):
            pass
        self.reply({"error": "없는 작업·파일"}, code=404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            if u.path == "/api/image":
                _, d = resolve(q["id"])
                ext = re.sub(r"[^a-z0-9]", "", q.get("ext", "png").lower())[:5] or "png"
                (d / "img").mkdir(exist_ok=True)
                name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "." + ext
                (d / "img" / name).write_bytes(raw)
                return self.reply({"path": "img/" + name})
            body = json.loads(raw or b"{}")
            tid = body["id"]
            f, d = resolve(tid)
            text = f.read_text(encoding="utf-8", errors="replace")
            if u.path == "/api/raw":
                if f.stat().st_mtime != body.get("mtime"):
                    return self.reply({"error": "그사이 문서가 바뀌었습니다 — 다시 불러온 뒤 고쳐 주세요"}, code=409)
                new = body["text"]
            elif u.path == "/api/answer":
                new = set_answer(text, body["item"], body["text"])
            elif u.path == "/api/memo":
                new = set_memo(text, body["text"])
            elif u.path == "/api/status":
                new = set_meta(text, "status", "끝" if body.get("done") else "진행 중")
            else:
                raise KeyError(u.path)
            if d is None and u.path != "/api/raw":
                return self.reply({"error": "txt 문서는 원문 편집만 됩니다"}, code=400)
            if new != text:
                write(f, new)
            if body.get("send") and d is not None:
                send(d, [body.get("item") or MEMO])
            return self.reply(detail(tid))
        except (KeyError, StopIteration, ValueError) as e:
            return self.reply({"error": f"요청 오류: {e}"}, code=400)


# ── 데스크톱 알림 ────────────────────────────────────────────────────────────

def pending():
    """지금 사용자 차례인 것 — 키가 새로 생기면 알린다(새 질문, 세션이 ⇒ 로 되물음, txt 에 빈 → 사용자 칸)."""
    out = {}
    for tid in all_ids():
        try:
            f, d = resolve(tid)
            if d is None:
                s = summary(tid)
                for i in range(s["mine"]):
                    out[f"{tid}#{i}"] = (tid, s["title"], "txt 문서", "")
                continue
            meta, body = split(f.read_text(encoding="utf-8", errors="replace"))
        except (KeyError, OSError):
            continue
        if meta.get("status", "") in DONE:
            continue
        for b in parse(body):
            if b["type"] == "item" and b["turn"] == "me":
                out[f"{tid}#{b['id']}#{len(b['entries'])}"] = (tid, meta.get("title") or tid, meta.get("session", ""),
                                                             f"{b['id']}. {b['title']}")
    return out


class Popups:
    """GNOME 알림. gi(libnotify)가 있으면 누르면 그 작업 화면이 열린다, 없으면 notify-send 로 글만."""

    def __init__(self):
        self.keep = set()
        try:
            import gi
            gi.require_version("Notify", "0.7")
            from gi.repository import GLib, Notify
            Notify.init("확인판")
            self.N, self.G = Notify, GLib
            threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
        except (ImportError, ValueError):
            self.N = None

    def show(self, tid, head, body):
        if self.N is None:
            subprocess.run(["notify-send", "-a", "확인판", head, body], check=False)
            return

        def go():
            n = self.N.Notification.new(head, body, "dialog-question")
            n.add_action("default", "열기", lambda *a: subprocess.Popen(["xdg-open", url(tid)]))
            n.connect("closed", lambda *a: self.keep.discard(n))
            self.keep.add(n)
            n.show()
            return False

        self.G.idle_add(go)


def notify_loop(interval=3):
    popups, prev = Popups(), pending()
    while True:
        time.sleep(interval)
        try:
            cur = pending()
        except Exception:   # 알림이 서버를 죽이면 안 된다 — 다음 바퀴에 다시
            continue
        new = {}
        for k, (tid, title, sess, q) in cur.items():
            if k not in prev:
                new.setdefault(tid, [title, sess, []])[2].append(q)
        prev = cur
        for tid, (title, sess, qs) in new.items():
            who = re.sub(r"\s*\[[0-9a-f]+\]$", "", sess) or "확인판"
            popups.show(tid, f"내 차례 · {who}", "\n".join([title] + [q for q in qs if q]))


def serve():
    BOARD.mkdir(parents=True, exist_ok=True)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    if NOTIFY:
        threading.Thread(target=notify_loop, daemon=True).start()
    print(f"확인판: http://localhost:{PORT}  (문서 {BOARD})", flush=True)
    srv.serve_forever()


# ── 명령 ─────────────────────────────────────────────────────────────────────

TEMPLATE = """---
title: {title}
session: {session}
status: 진행 중
---
## 요약
{summary}

## 정할 것

## 알려 드릴 것

## 메모
"""


def url(tid=""):
    return f"http://localhost:{PORT}/" + (("#" + urllib.parse.quote(tid)) if tid else "")


def cmd_new(a):
    d = BOARD / a.task
    if "/" in a.task or a.task.startswith("."):
        sys.exit("작업 이름에 / 나 맨 앞 . 은 쓸 수 없습니다")
    if (d / "task.md").exists():
        sys.exit(f"이미 있습니다: {d / 'task.md'}")
    d.mkdir(parents=True, exist_ok=True)
    write(d / "task.md", TEMPLATE.format(title=a.title, session=a.session, summary=a.summary or ""))
    print(f"{d / 'task.md'}\n{url(a.task)}")


def report(tid, d):
    info = json.loads((d / ".sent").read_text(encoding="utf-8"))
    print(f"[보냄 {info.get('at', '')}] {tid} — {d / 'task.md'}")
    blocks = parse(split((d / "task.md").read_text(encoding="utf-8"))[1])
    for b in blocks:
        if b["type"] == "item" and b["turn"] == "claude":
            print(f"  {b['id']}. {b['title']}\n    -> " + b["entries"][-1]["text"].replace("\n", "\n    -> "))
        if b["type"] == "memo" and MEMO in info.get("items", []) and b["text"].strip():
            print("  메모:\n    " + b["text"].strip().replace("\n", "\n    "))
    print("→ task.md 를 다시 읽고 질문마다 ⇒ 로 반영(결정된 건 [v]) 한 뒤 board wait 를 다시 건다.")


def cmd_wait(a):
    dirs = {}
    for t in a.task:
        _, d = resolve(t)
        if d is None:
            sys.exit(f"txt 문서는 기다릴 수 없습니다: {t}")
        dirs[t] = d
    deadline = time.time() + a.max * 60
    while time.time() < deadline:
        ready = []
        for t, d in dirs.items():
            sent, seen = d / ".sent", d / ".seen"
            if sent.exists() and (not seen.exists() or sent.stat().st_mtime_ns > seen.stat().st_mtime_ns):
                ready.append((t, d))
        if ready:
            for t, d in ready:
                report(t, d)
                (d / ".seen").write_text(now(), encoding="utf-8")
            return 0
        time.sleep(a.poll)
    print(f"{a.max}분 동안 [보내기] 없음 — board wait {' '.join(a.task)} 를 다시 건다.")
    return 3


def cmd_done(a):
    f, d = resolve(a.task)
    write(f, set_meta(f.read_text(encoding="utf-8"), "status", "끝"))
    print(f"끝으로 표시: {a.task}")


def cmd_list(a):
    for s in sorted((summary(t) for t in all_ids()), key=lambda s: (s["status"] == "끝", -s["mine"], -s["mtime"])):
        when = datetime.datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M")
        print(f"{s['status']:4}  내차례 {s['mine']:<2} {when}  {s['id']:<34} {s['title'][:40]}  [{s['session']}]")


def main(argv=None):
    p = argparse.ArgumentParser(prog="board", description="확인판 — 세션들이 사용자에게 물을 것을 작업별 탭으로")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    n = sub.add_parser("new")
    n.add_argument("task")
    n.add_argument("--title", required=True)
    n.add_argument("--session", required=True, help="ListAgents 맨 위의 이 세션 이름 [ref]")
    n.add_argument("--summary", default="")
    w = sub.add_parser("wait")
    w.add_argument("task", nargs="+")
    w.add_argument("--max", type=float, default=110, help="분 — 백그라운드 작업 상한(2시간) 안으로")
    w.add_argument("--poll", type=float, default=2)
    sub.add_parser("done").add_argument("task")
    sub.add_parser("list")
    sub.add_parser("url").add_argument("task", nargs="?", default="")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        return serve()
    if a.cmd == "url":
        return print(url(a.task))
    try:
        return {"new": cmd_new, "wait": cmd_wait, "done": cmd_done, "list": cmd_list}[a.cmd](a)
    except KeyError as e:
        sys.exit(f"없는 작업: {e} — board list 로 확인")


if __name__ == "__main__":
    sys.exit(main())
