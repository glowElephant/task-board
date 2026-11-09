#!/usr/bin/env python3
"""board — Task Board(옛 이름 확인판). 여러 Claude 세션이 사용자에게 물을 것을 작업별 문서(탭 하나)로 모은다.

파일이 원본이다: $BOARD_DIR/<작업>/task.md (+ img/). 서버는 그 파일을 보여 주고 사용자의 답·메모·이미지를
그 자리에 고쳐 넣을 뿐 상태를 따로 갖지 않는다. 사용자가 [보내기]를 누르면 <작업>/.sent 가 바뀌고,
서버가 그 작업 세션에 채널(이 서버의 /mcp)로 바로 넣는다. 채널 없이 띄운 세션은 `board wait` 로 기다린다.

  board serve                               화면 서버 (127.0.0.1:BOARD_PORT) + 채널 MCP(/mcp)
  board new <작업> --title T --session S    작업 문서 만들기 — 마지막 줄이 board wait 를 걸지 말지 알려 준다
  board wait <작업>... [--max 분]           (채널 없는 세션만) [보내기]까지 대기 — Bash run_in_background 로 건다
                                            exit 0 = 보냄(읽고 반영) · 3 = 시간 초과(다시 건다) · 4 = 사용자 자리 비움(폰 푸시 후 다시 건다)
                                            · 5 = 이 세션은 채널이 있어 안 기다림(다시 걸지 않는다)
  board done <작업>                         끝으로 표시 (목록 '끝난 작업' 으로)
  board list                                작업 목록
  board url [<작업>]                        화면 주소

설정: 환경변수 > ~/.config/board/env (KEY=VALUE). BOARD_DIR(기본 ~/board) · BOARD_PORT(기본 8770) ·
BOARD_EXTRA(옛 txt 문서 폴더, os.pathsep 로 여럿 — 탭으로 보여 주기만 한다) ·
BOARD_NOTIFY(0 이면 PC 알림 끔) · BOARD_AWAY_MIN(입력 없이 몇 분이면 자리 비움, 기본 30).
"""
import argparse
import base64
import datetime
import http.server
import json
import mimetypes
import os
import pathlib
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

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
AWAY_MIN = float(CFG.get("BOARD_AWAY_MIN") or 30)
WEB = pathlib.Path(__file__).resolve().parent / "web"
HUB = CFG.get("BOARD_HUB", "")              # 목록 아티팩트 주소 — 비면 아티팩트로 안 내보낸다
PC = CFG.get("BOARD_PC") or socket.gethostname()

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
        elif b["type"] == "memo":
            desc, ents = scan(lines)
            b["desc"] = "\n".join(desc).strip()
            b["entries"] = [{"who": e["who"], "text": e["text"]} for e in ents]
        else:
            b["text"] = "\n".join(lines).strip("\n")
    return out


def _item_range(lines, item_id):
    fz = fences(lines)
    start = next(i for i, l in enumerate(lines) if not fz[i] and (m := ITEM.match(l)) and m.group(2) == item_id)
    end = next((i for i in range(start + 1, len(lines)) if not fz[i] and HEAD.match(lines[i])), len(lines))
    return start, end


def add_answer(text, item_id, answer):
    """사용자 말을 질문(item_id) 또는 ## 메모(MEMO) 끝에 덧붙인다 — 화면은 보낸 칸을 비우고 대화에 쌓는다."""
    if not answer.strip():
        return text
    new = [("-> " + l) if l else "->" for l in answer.rstrip().split("\n")]
    lines = text.split("\n")
    fz = fences(lines)
    if item_id == MEMO:
        start = next((i for i, l in enumerate(lines) if not fz[i] and HEAD.match(l) and l.lstrip("#").strip() == MEMO), None)
        if start is None:
            while lines and not lines[-1].strip():
                lines.pop()
            return "\n".join(lines + ["", "## " + MEMO] + new + [""])
        end = next((j for j in range(start + 1, len(lines)) if not fz[j] and HEAD.match(lines[j])), len(lines))
    else:
        start, end = _item_range(lines, item_id)
    k = end
    while k > start + 1 and not lines[k - 1].strip():
        k -= 1
    lines[k:k] = new
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


_heard = {"at": 0.0, "ids": set()}


def listeners():
    """지금 `board wait` 가 듣고 있는 작업 — /proc 명령줄에서 읽는다(세션의 옛 코드 wait 도 잡힌다). 2초 캐시.
    Claude Code 는 메모리가 모자라면 쉬는 세션의 백그라운드 셸을 정리한다 — 그러면 [보내기]가 아무도 못 깨운다(11-04 실측 4개 세션)."""
    if time.time() - _heard["at"] < 2:
        return _heard["ids"]
    ids = set()
    for c in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = [x.decode(errors="replace") for x in c.read_bytes().split(b"\0") if x]
        except OSError:
            continue
        if "wait" not in args or not any(pathlib.Path(x).name in ("board", "board.py") for x in args[:args.index("wait")]):
            continue
        rest, i = args[args.index("wait") + 1:], 0
        while i < len(rest):
            if rest[i].startswith("--"):
                i += 1 if "=" in rest[i] else 2   # --max 110 / --max=110
                continue
            ids.add(rest[i])
            i += 1
    _heard.update(at=time.time(), ids=ids)
    return ids


def unread(d):
    sent, seen = d / ".sent", d / ".seen"
    return sent.exists() and (not seen.exists() or sent.stat().st_mtime_ns > seen.stat().st_mtime_ns)


def summary(tid):
    f, d = resolve(tid)
    text = f.read_text(encoding="utf-8", errors="replace")
    if d is None:
        mine = len(re.findall(r"^\s*→ 사용자[^:\n]*:\s*$", text, re.M))
        recent = time.time() - f.stat().st_mtime < 2 * 86400   # 옛 txt 는 끝 표시가 없다 — 이틀 손 안 대면 끝으로 본다
        return {"id": tid, "kind": "txt", "title": text.split("\n", 1)[0].strip()[:80] or f.name,
                "session": "txt 문서 · " + str(f.parent).replace(str(HOME), "~"),
                "status": "진행 중" if mine or re.search(r"^\s*\[[ ~]\]", text, re.M) or recent else "끝",
                "mine": mine, "waiting": 0, "mtime": f.stat().st_mtime,
                "asks": [m.group(1).strip() for m in re.finditer(r"^\s*\[ \]\s*(.+)$", text, re.M)]}
    meta, body = split(text)
    items = [b for b in parse(body) if b["type"] == "item"]
    return {"id": tid, "kind": "task", "title": meta.get("title") or tid, "session": meta.get("session", ""),
            "status": "끝" if meta.get("status", "") in DONE else "진행 중",
            "mine": sum(i["turn"] == "me" for i in items), "waiting": sum(i["turn"] == "claude" for i in items),
            "asks": [f"{i['id']}. {i['title']}" for i in items if i["turn"] == "me"],
            "listening": tid in listeners() or bool(chan_targets(meta)), "unread": unread(d), "unpushed": unpushed(tid), "mtime": f.stat().st_mtime}


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


# ── 채널: [보내기]를 세션에 바로 넣는다 ──────────────────────────────────────
# 세션은 이 서버의 /mcp(HTTP MCP, 이름 board)에 붙고, `--dangerously-load-development-channels server:board` 로 띄운
# 세션만 Claude Code 가 받아 깨어난다. 세션마다 프로세스를 띄우지 않는다 — 이 서버 하나가 모든 연결을 맡는다.
CHAN = "board"
CHAN_HELP = ('Task Board 에서 사용자가 [보내기]를 누르면 <channel source="board" task="<작업>"> 로 그 작업의 답·메모가 온다. '
             "사용자가 직접 보낸 것이니 채팅 답과 같이 다룬다 — 본문 끝 안내대로 task.md 를 다시 읽고 반영한다. "
             "이 세션은 board wait 를 걸지 않는다.")
IN_SERVER = False
_chans = {}            # MCP 세션 id → {"pid": Claude 프로세스, "q": 열린 스트림의 큐 또는 None}
_chans_lock = threading.Lock()
_cinfo = {"at": 0.0, "v": ([], {})}


def _proc(pid, name):
    return pathlib.Path(f"/proc/{pid}/{name}").read_bytes()


def claude_session(pid):
    """Claude Code 가 <설정 폴더>/sessions/<pid>.json 에 적는 세션 정보(sessionId·name). 아니면 None."""
    try:
        env = dict(x.split("=", 1) for x in _proc(pid, "environ").decode(errors="replace").split("\0") if "=" in x)
        cfg = pathlib.Path(env.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
        return json.loads((cfg / "sessions" / f"{pid}.json").read_text(encoding="utf-8")) | {"_dir": str(cfg)}
    except (OSError, ValueError, TypeError):
        return None


def claude_pid(pid=None):
    """이 프로세스를 띄운 Claude 세션의 pid — 부모를 거슬러 올라가 세션 파일이 있는 곳."""
    pid = pid or os.getppid()
    for _ in range(8):
        if pid <= 1:
            return None
        if claude_session(pid):
            return pid
        try:
            pid = int(_proc(pid, "stat").decode().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
    return None


def chan_on(pid):
    """채널을 켜고 띄운 세션인가 — 플래그 없이 붙은 연결에 넣으면 Claude Code 가 조용히 버린다."""
    try:
        return f"server:{CHAN}".encode() in _proc(pid, "cmdline").split(b"\0")
    except OSError:
        return False


def chan_info():
    """([(MCP 세션 id, pid, 세션 정보)] 넣을 수 있는 연결, {세션 이름: 살아 있는 수}). 2초 캐시."""
    if time.time() - _cinfo["at"] < 2:
        return _cinfo["v"]
    if IN_SERVER:
        with _chans_lock:
            conns = [(m, c["pid"]) for m, c in _chans.items() if c["q"] is not None]
    else:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/channels", timeout=2) as r:
                conns = [tuple(x) for x in json.load(r)]
        except (OSError, ValueError):
            conns = []
    live = [(m, p, s) for m, p in conns if chan_on(p) and (s := claude_session(p))]
    names = {}
    for sd in {pathlib.Path(s["_dir"]) for _, _, s in live} | {HOME / ".claude"}:
        for f in (sd / "sessions").glob("*.json"):
            if pathlib.Path(f"/proc/{f.stem}").exists():
                try:
                    n = json.loads(f.read_text(encoding="utf-8")).get("name", "")
                except (OSError, ValueError):
                    continue
                names[n] = names.get(n, 0) + 1
    _cinfo.update(at=time.time(), v=(live, names))
    return live, names


def chan_targets(meta):
    """이 작업을 받을 연결 — 문서의 sid 와 같은 세션, 없으면 이름이 하나뿐인 세션(같은 이름이 여럿이면 안 보낸다: 잘못 가느니 안 간다)."""
    live, names = chan_info()
    sid = meta.get("sid", "")
    hit = [m for m, _, s in live if sid and s.get("sessionId") == sid]
    name = re.sub(r"\s*\[[0-9a-f]+\]\s*$", "", meta.get("session", ""))
    if not hit and name and names.get(name) == 1:
        hit = [m for m, _, s in live if s.get("name") == name]
    return hit


def chan_here():
    """이 명령을 부른 세션이 채널로 [보내기]를 바로 받는가."""
    pid = claude_pid()
    return bool(pid) and any(p == pid for _, p, _ in chan_info()[0])


def wait_hint(tids):
    if chan_here():
        return "이 세션은 채널로 연결돼 있어 [보내기]가 바로 들어온다 — board wait 는 걸지 않는다."
    return f"이 세션은 채널이 없다 — board wait {' '.join(tids)} 를 Bash run_in_background 로 건다(채널을 켜고 다시 띄우면 필요 없다)."


def claim(f):
    """문서에 이 세션 id 를 적는다 — 채널이 이름 대신 id 로 세션을 찾게(이름은 겹치고 바뀐다)."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    text = f.read_text(encoding="utf-8")
    if sid and split(text)[0].get("sid") != sid:
        write(f, set_meta(text, "sid", sid))


def _chan_put(hits, tid, content):
    msg = {"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {"content": content, "meta": {"task": tid}}}
    sent = 0
    with _chans_lock:
        for m in hits:
            if _chans.get(m, {}).get("q") is not None:
                _chans[m]["q"].put(msg)
                sent += 1
    return sent


def deliver(away=None):
    """안 읽힌 [보내기]를 그 작업 세션에 넣고 읽음 처리. 채널이 없는 세션 것은 그대로 둔다(board wait 가 받는다).
    away(자리 비움일 때의 presence)를 주면 폰 알림 할 일도 넣는다 — 채널 세션엔 board wait exit 4 가 없어서."""
    for tid in all_ids():
        if tid.startswith("txt:"):
            continue
        try:
            f, d = resolve(tid)
            new = unread(d)
            if not new and not away:
                continue
            hits = chan_targets(split(f.read_text(encoding="utf-8", errors="replace"))[0])
            if not hits:
                continue
            if new and (sent := _chan_put(hits, tid, report_text(tid, d, channel=True))):
                (d / ".seen").write_text(now(), encoding="utf-8")
                print(f"[채널] {tid} → {sent}곳", flush=True)
            if away and (due := away_due({tid: d}, away)):
                _chan_put(hits, tid, "[자리 비움] 사용자가 자리를 비웠고 이 작업에 내 차례가 남아 있다 — "
                          "PushNotification(status=proactive) 으로 아래 한 줄을 그대로 보낸다. board wait 는 걸지 않는다.\n"
                          f"Task Board · {due[0][0][:40]}: 내 차례 {due[0][1]}건")
                print(f"[채널] {tid} 자리 비움 알림", flush=True)
        except (KeyError, OSError, ValueError) as e:
            print(f"[채널] {tid}: {e}", flush=True)


def chan_loop(interval=1.5, away_every=20):
    n = 0
    while True:
        time.sleep(interval)
        n += 1
        try:
            p = presence() if n % away_every == 0 else None   # 자리 비움은 분 단위라 30초마다면 된다(gdbus 를 덜 부른다)
            deliver(p if p and p["state"] == "away" else None)
            with _chans_lock:
                for m in [m for m, c in _chans.items() if c["q"] is None and not pathlib.Path(f"/proc/{c['pid']}").exists()]:
                    del _chans[m]
        except Exception as e:   # 이 줄이 죽으면 [보내기]가 다시 아무에게도 안 간다
            print(f"[채널] {e!r}", flush=True)


# ── 서버 ─────────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body, ctype="application/json; charset=utf-8", code=200, headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def mcp_post(self, raw):
        """board MCP — 도구 없이 채널만. 처음 붙을 때 Claude Code 가 X-Board-Pid(board channel-headers)로 자기 pid 를 알린다."""
        msg = json.loads(raw or b"{}")
        if not isinstance(msg, dict) or msg.get("id") is None:      # 알림·응답·묶음은 받기만
            return self.reply(b"", code=202)
        m, mid, hdr = msg.get("method"), msg["id"], {}
        if m == "initialize":
            sid = os.urandom(16).hex()
            try:
                pid = int(self.headers.get("X-Board-Pid") or 0)
            except ValueError:
                pid = 0
            with _chans_lock:
                _chans[sid] = {"pid": pid, "q": None}
            hdr["Mcp-Session-Id"] = sid
            # 2026-07-28 판(modern)은 서버가 먼저 보낼 길이 없어 Claude Code 가 채널을 안 붙인다 — 그 전 판으로 답한다
            out = {"protocolVersion": "2025-06-18", "capabilities": {"experimental": {"claude/channel": {}}},
                   "serverInfo": {"name": CHAN, "version": "1"}, "instructions": CHAN_HELP}
            out = {"jsonrpc": "2.0", "id": mid, "result": out}
        elif m == "ping":
            out = {"jsonrpc": "2.0", "id": mid, "result": {}}
        else:
            out = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"없는 메서드: {m}"}}
        self.reply(out, headers=hdr)

    def mcp_stream(self):
        """서버 → 세션 스트림(SSE). 연결마다 스레드 하나가 큐를 기다린다 — 세션 쪽 프로세스는 없다."""
        sid = self.headers.get("Mcp-Session-Id", "")
        with _chans_lock:
            c = _chans.get(sid)
            if c is not None:
                q = c["q"] = queue.Queue()
        if c is None:
            return self.reply({"error": "없는 MCP 세션 — 다시 initialize"}, code=404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(b": ok\n\n")
            while True:
                self.wfile.flush()
                try:
                    ev = q.get(timeout=25)
                    self.wfile.write(b"event: message\ndata: " + json.dumps(ev, ensure_ascii=False).encode() + b"\n\n")
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
        except OSError:
            pass
        finally:
            with _chans_lock:
                if _chans.get(sid, {}).get("q") is q:
                    _chans[sid]["q"] = None

    def do_DELETE(self):
        with _chans_lock:
            _chans.pop(self.headers.get("Mcp-Session-Id", ""), None)
        self.reply(b"", code=200)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path == "/mcp":
                return self.mcp_stream()
            if u.path == "/api/channels":
                with _chans_lock:
                    return self.reply([[m, c["pid"]] for m, c in _chans.items() if c["q"] is not None])
            if u.path in ("/", "/index.html"):
                return self.reply((WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/presence":
                return self.reply(presence())
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
            if u.path == "/mcp":
                return self.mcp_post(raw)
            if u.path == "/api/presence":
                _seen["beat"] = time.time()
                _seen["at"] = 0.0
                return self.reply(presence())
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
                new = add_answer(text, body["item"], body["text"])
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
            Notify.init("Task Board")
            self.N, self.G = Notify, GLib
            threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
        except (ImportError, ValueError):
            self.N = None

    def show(self, tid, head, body):
        if self.N is None:
            subprocess.run(["notify-send", "-u", "critical", "-a", "Task Board", head, body], check=False)
            return

        def go():
            n = self.N.Notification.new(head, body, "dialog-question")
            n.set_urgency(self.N.Urgency.CRITICAL)   # 방해 금지 중에도 배너로 뜬다(11-03 사용자 Q7 A). 누르거나 닫을 때까지 남는다
            n.add_action("default", "열기", lambda *a: subprocess.Popen(["xdg-open", url(tid)]))
            n.connect("closed", lambda *a: self.keep.discard(n))
            self.keep.add(n)
            n.show()
            return False

        self.G.idle_add(go)


_seen = {"beat": 0.0, "away_since": None, "at": 0.0, "state": "desk"}


def _gdbus(dest, path, method):
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", dest, "--object-path", path, "--method", method],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"\((?:uint64 )?(\w+),\)", out)
    return m.group(1) if m else None


def presence():
    """watching = Task Board 창을 보는 중 · desk = PC 앞(다른 일) · away = AWAY_MIN 분 넘게 키보드·마우스 입력 없음.
    잠금은 따로 안 본다 — 잠그자마자 폰이 울리면 잠깐 자리 뜰 때도 울려서 30분 뒤부터로 했다(11-03 사용자).
    잠겨 있어도 유휴 시간은 계속 쌓인다. GNOME 이 아니면 유휴를 못 읽어 desk 로 본다."""
    t = time.time()
    if t - _seen["at"] < 1.5:
        return {"state": _seen["state"], "away_since": _seen["away_since"]}
    idle = _gdbus("org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core", "org.gnome.Mutter.IdleMonitor.GetIdletime")
    idle_s = int(idle) / 1000 if idle and idle.isdigit() else 0
    if idle_s >= AWAY_MIN * 60:
        state = "away"
    elif t - _seen["beat"] < 12:
        state = "watching"
    else:
        state = "desk"
    if state == "away" and _seen["away_since"] is None:
        _seen["away_since"] = round(t - idle_s)
    elif state != "away":
        _seen["away_since"] = None
    _seen.update(at=t, state=state)
    return {"state": state, "away_since": _seen["away_since"]}


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
        if new and presence()["state"] != "desk":   # 보고 있으면 화면 숫자로 충분, 자리 비움은 세션이 폰으로
            continue
        for tid, (title, sess, qs) in new.items():
            who = re.sub(r"\s*\[[0-9a-f]+\]$", "", sess) or "Task Board"
            popups.show(tid, f"내 차례 · {who}", "\n".join([title] + [q for q in qs if q]))


def serve():
    global IN_SERVER
    IN_SERVER = True
    BOARD.mkdir(parents=True, exist_ok=True)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=chan_loop, daemon=True).start()
    if NOTIFY:
        threading.Thread(target=notify_loop, daemon=True).start()
    print(f"Task Board: http://localhost:{PORT}  (문서 {BOARD})", flush=True)
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
    claim(d / "task.md")
    print(f"{d / 'task.md'}\n{url(a.task)}\n{wait_hint([a.task])}")


def report_text(tid, d, channel=False):
    info = json.loads((d / ".sent").read_text(encoding="utf-8"))
    out = [f"[보냄 {info.get('at', '')}] {tid} — {d / 'task.md'}"]
    blocks = parse(split((d / "task.md").read_text(encoding="utf-8"))[1])
    for b in blocks:
        if b["type"] == "item" and b["turn"] == "claude":
            out.append(f"  {b['id']}. {b['title']}\n    -> " + b["entries"][-1]["text"].replace("\n", "\n    -> "))
        if b["type"] == "memo" and MEMO in info.get("items", []) and b["entries"] and b["entries"][-1]["who"] == "me":
            out.append("  메모:\n    -> " + b["entries"][-1]["text"].replace("\n", "\n    -> "))
    push = HUB and not local_only(d / "task.md")
    out.append("→ task.md 를 다시 읽고 질문마다 ⇒ 로 반영(결정된 건 [v]) 한 뒤" + (f" board push {tid} 로 아티팩트에도 올린다." if push else " 끝.")
               + (" 채널로 받았으니 board wait 는 걸지 않는다." if channel else " 그리고 board wait 를 다시 건다."))
    return "\n".join(out)


def report(tid, d):
    print(report_text(tid, d))


def cmd_wait(a):
    dirs = {}
    for t in a.task:
        _, d = resolve(t)
        if d is None:
            sys.exit(f"txt 문서는 기다릴 수 없습니다: {t}")
        dirs[t] = d
        claim(d / "task.md")
    if chan_here():
        print(wait_hint(a.task) + " 안 읽은 [보내기]가 있으면 곧 채널로 들어온다.")
        return 5
    deadline = time.time() + a.max * 60
    while time.time() < deadline:
        ready = []
        for t, d in dirs.items():
            if unread(d):
                ready.append((t, d))
        if ready:
            for t, d in ready:
                report(t, d)
                (d / ".seen").write_text(now(), encoding="utf-8")
            return 0
        due = away_due(dirs)
        if due:
            print("[자리 비움] 사용자가 자리를 비웠고 내 차례가 남아 있다 — PushNotification(status=proactive) 으로 아래 한 줄을 보내고 board wait 를 다시 건다.")
            print("Task Board · " + " / ".join(f"{title[:40]}: 내 차례 {n}건" for title, n in due))
            return 4
        time.sleep(a.poll)
    print(f"{a.max}분 동안 [보내기] 없음 — board wait {' '.join(a.task)} 를 다시 건다.")
    return 3


def away_due(dirs, p=None):
    """자리 비움 동안 작업마다 한 번(내 차례가 늘면 다시) 폰으로 알릴 것. 서버가 꺼져 있으면 알리지 않는다.
    p = presence() 결과(서버 안에서 부를 때), 없으면 서버에 묻는다."""
    if p is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/presence", timeout=2) as r:
                p = json.load(r)
        except (OSError, ValueError):
            return []
    if p.get("state") != "away":
        return []
    due = []
    for t, d in dirs.items():
        s = summary(t)
        mark = d / ".pushed"
        try:
            last = json.loads(mark.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = {}
        if s["status"] != "끝" and s["mine"] and (last.get("since") != p["away_since"] or s["mine"] > last.get("mine", 0)):
            mark.write_text(json.dumps({"since": p["away_since"], "mine": s["mine"]}), encoding="utf-8")
            due.append((s["title"], s["mine"]))
    return due


def cmd_done(a):
    f, d = resolve(a.task)
    write(f, set_meta(f.read_text(encoding="utf-8"), "status", "끝"))
    print(f"끝으로 표시: {a.task}")


def cmd_orphans(a):
    """보냈는데 듣는 세션이 없어 안 읽힌 작업 — 아무 세션이나 이걸 보고 그 세션에 SendMessage 로 깨워 준다."""
    found = 0
    for t in all_ids():
        s = summary(t)
        if s.get("unread") and not s.get("listening"):
            found += 1
            print(f"{t}\t{s['session']}\t{s['title'][:60]}")
            print(f"  SendMessage → {s['session']}: Task Board {t} 에 사용자가 [보내기] 했는데 이 세션의 board wait 가 꺼져 있어 못 깼습니다."
                  f" `board wait {t}` 를 Bash run_in_background 로 다시 걸어 주세요 — 바로 끝나며 보낸 내용을 보여 줍니다.")
    if not found:
        print("안 읽힌 작업 없음")


IMG_ONE, IMG_ALL = 3_000_000, 10_000_000


def session_key(session):
    """세션 아티팩트 키 — '버그 고치기 [756bdf]' → '756bdf'(이름이 바뀌어도 ref 는 그대로). ref 가 없으면 이름을 안전한 글자로."""
    m = re.search(r"\[([0-9a-f]+)\]\s*$", session or "")
    return m.group(1) if m else (re.sub(r"[^A-Za-z0-9_-]", "_", session or "unknown")[:60] or "unknown")


def session_tasks(session):
    return [t for t in all_ids() if not t.startswith("txt:") and summary(t)["session"] == session and summary(t)["status"] != "끝"
            and not local_only(resolve(t)[0])]


def page_for(session, also=()):
    """세션 아티팩트 한 장 — PC 화면과 같은 코드(web/index.html)에 그 세션의 진행 중 작업만 심는다(window.BOARD.mode='session').
    세션마다 한 장인 이유(11-04 사용자 "같은 세션끼리만 두면 되잖아"): [보내기]는 아티팩트를 올린 세션만 깨우니 세션 단위면 충분하고,
    작업마다 따로면 아티팩트가 너무 많아지고 처음 보내기 허락도 작업마다 묻는다. 사진은 data: 로(장당 3 MB · 합쳐 10 MB)."""
    ids = list(dict.fromkeys(session_tasks(session) + [t for t in also if t not in session_tasks(session)]))
    tasks, details, images, total = [], {}, {}, 0
    for tid in ids:
        d = detail(tid)
        text = d.pop("raw")
        tasks.append({k: v for k, v in d.items() if k != "blocks"})
        details[tid] = d
        folder = resolve(tid)[1]
        for rel in sorted(set(re.findall(r"!\[[^\]]*\]\((img/[^)\s]+)\)", text))):
            f = folder / rel
            if f.is_file() and f.stat().st_size < IMG_ONE and total + f.stat().st_size < IMG_ALL:
                total += f.stat().st_size
                mime = mimetypes.guess_type(f.name)[0] or "image/png"
                images[f"{tid}/{rel}"] = f"data:{mime};base64," + base64.b64encode(f.read_bytes()).decode()
    data = {"mode": "session", "session": session, "hub": HUB, "pc": PC, "tasks": tasks, "details": details, "images": images, "at": time.time()}
    page = (WEB / "index.html").read_text(encoding="utf-8")
    name = re.sub(r"[<>&]", "", re.sub(r"\s*\[[0-9a-f]+\]$", "", session or ""))[:60]
    page = page.replace("<title>Task Board</title>", f"<title>Task Board · {name}</title>", 1)
    js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return page.replace("<script>\nconst $ =", "<script>window.BOARD = " + js + ";</script>\n<script>\nconst $ =", 1)


def sess_state(session, upd=None):
    f = BOARD / ".sessions" / (session_key(session) + ".json")
    try:
        st = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if upd is not None:
        st.update(upd)
        f.parent.mkdir(exist_ok=True)
        write(f, json.dumps(st, ensure_ascii=False))
    return st


def row_id(tid):
    """목록 저장소 문서 id — PC 이름을 붙여 다른 PC 의 같은 작업 이름과 안 겹치게. 허용 글자 밖(한글 등)은 ~xxxx 로."""
    return re.sub(r"[^A-Za-z0-9_\-.]", lambda m: "~%04x" % ord(m.group()), f"{PC}__{tid}")[:200]


def remote(d):
    """작업 폴더의 .remote/ — 아티팩트 주소(url)·목록 줄 버전(version)·올린 시각(at)."""
    try:
        return json.loads((d / ".remote" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def local_only(f):
    """문서 머리 `local: yes` — 회사 내부 자료(결재 번호·금액·사람 이름 등)가 있어 아티팩트(claude.ai)로 안 보내는 작업.
    2026-11-04 회사 결재 문서를 다루는 작업에서 요청됨."""
    return split(f.read_text(encoding="utf-8", errors="replace"))[0].get("local", "").lower() in ("yes", "true", "1", "예")


def unpushed(tid):
    """문서가 마지막으로 아티팩트에 올린 뒤 바뀌었나(목록 주소가 없거나 로컬 전용이면 늘 False)."""
    if not HUB or tid.startswith("txt:"):
        return False
    f, d = resolve(tid)
    if local_only(f):
        return False
    return f.stat().st_mtime > remote(d).get("at", 0)


def cmd_push(a):
    """세션 아티팩트(그 세션의 진행 중 작업 전부)·목록 줄을 내보낼 준비를 하고, 세션이 부를 도구 호출을 순서대로 찍는다.
    세션이 문서를 고친 그 자리에서 부른다 — 중계 세션 없이 두 곳(로컬 보드·아티팩트)이 같은 문서에서 그려진다(11-04 사용자 결정)."""
    if not HUB:
        sys.exit("BOARD_HUB(목록 아티팩트 주소)가 설정에 없습니다 — ~/.config/board/env")
    f, d = resolve(a.task)
    if d is None:
        sys.exit("txt 문서는 안 됩니다")
    if local_only(f):
        sys.exit(f"{a.task} 는 로컬 전용(local: yes)이라 아티팩트로 안 보냅니다.")
    claim(f)
    s = summary(a.task)
    if not s["session"]:
        sys.exit("문서 머리에 session: 이 없습니다 — '이름 [ref]' 를 넣고 다시")
    out = pathlib.Path(a.out).expanduser() if a.out else BOARD / ".sessions"
    out.mkdir(parents=True, exist_ok=True)
    ss, key = sess_state(s["session"]), session_key(s["session"])
    page = out / f"{key}.html"
    write(page, page_for(s["session"], also=[a.task]))   # 방금 끝낸 작업도 이번 한 번은 '끝'으로 보이게
    st = remote(d)
    url = ss.get("url", "")
    row = {"pc": PC, "task": a.task, "title": s["title"], "session": s["session"], "status": s["status"],
           "mine": s["mine"], "waiting": s["waiting"], "asks": s["asks"][:5], "url": (url + "#" + a.task) if url else "", "updated": time.time(),
           "blocks": detail(a.task)["blocks"]}   # 목록이 PC 화면처럼 오른쪽에 내용을 그린다(답은 세션 아티팩트에서)
    (d / ".remote").mkdir(exist_ok=True)
    write(d / ".remote" / "row.json", json.dumps(row, ensure_ascii=False))
    print(f"[push] {a.task} — 세션 아티팩트({s['session']})·목록 줄. 아래 순서대로 부르세요.")
    if url:   # 막 만든 아티팩트는 연결이 붙기 전 온 댓글을 놓칠 수 있다(11-04 실측) — 올릴 때마다 먼저 줍는다
        print(f"0) ArtifactComments read: url={url} — 열린 스레드의 [board …] 중 문서에 아직 없는 것을 board reply 로 먼저 넣고 resolve")
        print(f"1) Artifact publish: file_path={page}  url={url}")
    else:
        print(f'1) Artifact publish: file_path={page}  icon="checklist"  capabilities={{"comments": {{}}, "assets": {{}}}}  (이 세션의 첫 아티팩트)')
        print(f"   → board pushed {a.task} --url <결과 주소>   (세션 아티팩트 주소 기록 + 목록 줄 링크)")
    v = st.get("version")
    print(f"2) ArtifactData set: url={HUB}  collection=tasks  doc_id={row_id(a.task)}  file_path={d / '.remote' / 'row.json'}" + (f"  if_version={v}" if v else "  (처음 — if_version 없이)"))
    print(f"3) board pushed {a.task} --version <결과 version>")
    print("   버전이 안 맞아 거절되면 결과에 나온 현재 version 으로 2) 를 다시. 아티팩트가 없다고 나오면 board pushed <작업> --reset 뒤 다시 push.")
    print(wait_hint([a.task]))


def _set_cfg(key, val):
    f = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "board" / "env"
    f.parent.mkdir(parents=True, exist_ok=True)
    lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
    lines = [ln for ln in lines if not ln.startswith(key + "=")] + [f"{key}={val}"]
    write(f, "\n".join(lines) + "\n")


def cmd_setup(a):
    """처음 한 번(여러 번 해도 같음) — 설정 파일 · board 명령 링크 · PC 화면(리눅스 systemd 사용자 서비스) · 목록 아티팩트 안내.
    플러그인으로 깔았으면 세션 시작 훅이 이걸 안내한다. 이 PC 처럼 install.sh 로 깔면 install.sh 가 부른다."""
    here = pathlib.Path(__file__).resolve().parent
    cfgf = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "board" / "env"
    if not cfgf.is_file():
        cfgf.parent.mkdir(parents=True, exist_ok=True)
        write(cfgf, f"BOARD_DIR={HOME / 'board'}\nBOARD_PORT=8770\n# 목록 아티팩트(모든 PC 의 작업) — board setup --hub <주소>\nBOARD_HUB=\n")
        print(f"설정 만듦: {cfgf}")
    if a.hub:
        _set_cfg("BOARD_HUB", a.hub)
        print(f"목록 아티팩트: {a.hub}")
    if a.pc:
        _set_cfg("BOARD_PC", a.pc)
    bindir = pathlib.Path(os.environ.get("XDG_BIN_HOME") or HOME / ".local" / "bin")
    bindir.mkdir(parents=True, exist_ok=True)
    (here / "board.py").chmod(0o755)
    link = bindir / "board"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(here / "board.py")
    print(f"명령: {link} → {here / 'board.py'}" + ("" if shutil.which("board") else f"  (⚠ {bindir} 가 PATH 에 없음)"))
    if sys.platform.startswith("linux") and shutil.which("systemctl") and not a.no_server:
        unit = pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "systemd" / "user" / "board.service"
        unit.parent.mkdir(parents=True, exist_ok=True)
        write(unit, f"[Unit]\nDescription=board (Task Board)\n\n[Service]\nExecStart=/usr/bin/env python3 {here / 'board.py'} serve\nRestart=on-failure\n\n[Install]\nWantedBy=default.target\n")
        for cmd in (["daemon-reload"], ["enable", "board.service"], ["restart", "board.service"]):
            subprocess.run(["systemctl", "--user", *cmd], check=False, capture_output=True)
        print(f"PC 화면: http://localhost:{PORT}  (사용자 서비스 board)")
    elif not a.no_server:
        print(f"PC 화면은 수동 — `python3 {here / 'board.py'} serve` (리눅스가 아니면 자리 비움·PC 알림은 아직 안 됨). 아티팩트는 그대로 쓴다.")
    if shutil.which("claude") and sys.platform.startswith("linux"):   # 채널은 /proc 로 세션을 찾는다 — 리눅스만
        spec = {"type": "http", "url": f"http://127.0.0.1:{PORT}/mcp", "headersHelper": f"{link} channel-headers"}
        subprocess.run(["claude", "mcp", "remove", CHAN, "-s", "user"], capture_output=True)
        r = subprocess.run(["claude", "mcp", "add-json", CHAN, json.dumps(spec), "-s", "user"], capture_output=True, text=True)
        print(f"채널 MCP '{CHAN}' 등록" + (" 함" if r.returncode == 0 else f" 실패: {(r.stderr or r.stdout).strip()[:200]}"))
        print(f"  세션을 `claude --dangerously-load-development-channels server:{CHAN}` 로 띄우면 [보내기]가 바로 들어온다(board wait 불필요).")
        print(f"  매번 붙이기 싫으면 래퍼를 PATH 맨 앞에(대화형 실행에만 붙인다, -p·하위 명령은 그대로) — ~/.bashrc 와 ~/.profile 끝에:\n"
              f'    [ -x "{here / "bin" / "claude"}" ] && PATH="{here / "bin"}:$PATH"')
    hub = a.hub or _config().get("BOARD_HUB", "")
    if not hub:
        page = cfgf.parent / "hub.html"
        a2 = argparse.Namespace(out=str(page))
        cmd_hubpage(a2)
        print("목록 아티팩트(밖에서 보기)가 아직 없습니다 — 세션이 이렇게 만든다(한 번만):")
        print(f'  Artifact publish: file_path={page}  icon="checklist"  capabilities={{"db": {{}}}}')
        print("  → board setup --hub <결과 주소>")


def cmd_hubpage(a):
    """목록 아티팩트 화면 — PC 화면과 같은 코드(web/index.html)를 목록 모드로. 데이터는 심지 않고 db tasks 를 실시간으로 읽는다.
    화면 코드를 고쳤을 때만 다시 올린다(이 PC 의 목록 담당 세션이 BOARD_HUB 주소로 publish, capabilities={"db": {}})."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    page = page.replace("<script>\nconst $ =", "<script>window.BOARD = " + json.dumps({"mode": "hub", "hub": HUB}) + ";</script>\n<script>\nconst $ =", 1)
    out = pathlib.Path(a.out).expanduser()
    write(out, page)
    print(out)


def cmd_pushed(a):
    f, d = resolve(a.task)
    (d / ".remote").mkdir(exist_ok=True)
    sess = summary(a.task)["session"]
    st = remote(d)
    if a.reset:
        st.pop("version", None)
        sess_state(sess, {"url": ""})
    if a.url:
        sess_state(sess, {"url": a.url})
        rowf = d / ".remote" / "row.json"
        if rowf.is_file():
            row = json.loads(rowf.read_text(encoding="utf-8")); row["url"] = a.url + "#" + a.task
            write(rowf, json.dumps(row, ensure_ascii=False))
    if a.version:
        st["version"] = a.version
    if a.url or a.version:
        st["at"] = time.time()
    write(d / ".remote" / "state.json", json.dumps(st))
    print(f"기록함: {a.task} {st} · 세션 {sess_state(sess)}")


def cmd_reply(a):
    """바깥 화면(아티팩트)의 [보내기]·[저장]을 파일에 써 넣는다(댓글을 받은 세션이 부른다). 글 속 ![](로컬 파일) 은 작업 img/ 로 옮긴다.
    부른 세션이 그 작업 담당(sid 같음)이면 이미 받은 것이라 읽음까지 한다 — 안 하면 채널·board wait 가 같은 답으로 또 깨운다.
    담당이 아닌데 담당 세션이 안 듣고 있으면 SendMessage 로 깨울 문구를 찍는다."""
    f, d = resolve(a.task)
    if d is None:
        sys.exit("txt 문서는 안 됩니다")
    answer = pathlib.Path(a.file).read_text(encoding="utf-8") if a.file else sys.stdin.read()

    def move(m):
        src = pathlib.Path(m.group(2)).expanduser()
        if not src.is_absolute() or not src.is_file():
            return m.group(0)
        (d / "img").mkdir(exist_ok=True)
        name = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + (src.suffix or ".png")
        shutil.copyfile(src, d / "img" / name)
        return f"![{m.group(1)}](img/{name})"
    answer = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", move, answer)
    text = f.read_text(encoding="utf-8")
    new = add_answer(text, a.item, answer)
    if new == text:
        sys.exit("빈 답 — 아무것도 안 바꿨습니다")
    write(f, new)
    if not a.send:
        return print(f"저장함: {a.task} {a.item}")
    sid = split(new)[0].get("sid", "")
    pending = unread(d)   # PC 화면에서 보낸 게 아직 안 갔으면 읽음 처리하지 않는다 — 그것까지 삼키면 안 된다
    send(d, [a.item])
    if sid and sid == os.environ.get("CLAUDE_CODE_SESSION_ID") and not pending:
        (d / ".seen").write_text(now(), encoding="utf-8")
        return print(f"보냄: {a.task} {a.item} — 이 세션이 받은 답이라 다시 깨우지 않는다. task.md 를 다시 Read 하고 ⇒ 로 반영한다.")
    s = summary(a.task)
    if s["listening"]:
        print(f"보냄: {a.task} {a.item} — {s['session']} 가 듣는 중(채널 또는 board wait)이라 곧 깨어납니다.")
    else:
        print(f"보냄: {a.task} {a.item} — {s['session']} 가 안 듣는 중. SendMessage 로 깨운다:")
        print(f"  Task Board {a.task} 에 사용자가 [보내기] 했는데 이 세션의 board wait 가 꺼져 있습니다."
              f" `board wait {a.task}` 를 Bash run_in_background 로 다시 걸어 주세요 — 바로 끝나며 보낸 내용을 보여 줍니다.")


def cmd_channel_headers(a):
    """Claude Code 가 board MCP 에 붙을 때 부른다(headersHelper) — 이 세션의 Claude pid 를 머리글로 알린다."""
    print(json.dumps({"X-Board-Pid": str(claude_pid() or 0)}))


def cmd_list(a):
    for s in sorted((summary(t) for t in all_ids()), key=lambda s: (s["status"] == "끝", -s["mine"], -s["mtime"])):
        when = datetime.datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M")
        print(f"{s['status']:4}  내차례 {s['mine']:<2} {when}  {s['id']:<34} {s['title'][:40]}  [{s['session']}]")


def main(argv=None):
    p = argparse.ArgumentParser(prog="board", description="Task Board — 세션들이 사용자에게 물을 것을 작업별 탭으로")
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
    sub.add_parser("orphans", help="보냈는데 듣는 세션이 없어 안 읽힌 작업과 깨울 메시지")
    pu = sub.add_parser("push", help="작업 아티팩트·목록 줄 내보낼 준비 + 부를 도구 호출 안내")
    pu.add_argument("task")
    pu.add_argument("--out", help="만들 파일을 둘 폴더(기본 <작업>/.remote) — Artifact 가 거절하면 세션 scratchpad 로")
    sub.add_parser("hubpage", help="목록 아티팩트 화면 만들기(화면 코드 고쳤을 때만)").add_argument("out")
    su = sub.add_parser("setup", help="처음 설정 — 설정 파일·명령 링크·PC 화면·목록 아티팩트 안내(여러 번 해도 같음)")
    su.add_argument("--hub", help="목록 아티팩트 주소를 저장")
    su.add_argument("--pc", help="이 PC 이름(기본 hostname)")
    su.add_argument("--no-server", action="store_true", help="PC 화면(서버)은 안 켬")
    pd = sub.add_parser("pushed", help="push 뒤 결과(아티팩트 주소·목록 줄 버전) 기록")
    pd.add_argument("task")
    pd.add_argument("--url")
    pd.add_argument("--version", type=int)
    pd.add_argument("--reset", action="store_true", help="아티팩트가 지워졌을 때 — 주소·버전을 잊는다")
    rp = sub.add_parser("reply", help="바깥 화면에서 온 답을 문서에 넣기")
    rp.add_argument("task")
    rp.add_argument("item", help="Q1 같은 질문 id, 또는 메모")
    rp.add_argument("--file", help="답 글 파일(없으면 표준 입력)")
    rp.add_argument("--save", dest="send", action="store_false", help="보내지 않고 문서에만")
    sub.add_parser("url").add_argument("task", nargs="?", default="")
    sub.add_parser("channel-headers", help="(Claude Code 가 부름) board MCP 에 붙을 때 세션 pid 머리글")
    a = p.parse_args(argv)
    if a.cmd == "serve":
        return serve()
    if a.cmd == "url":
        return print(url(a.task))
    try:
        return {"new": cmd_new, "wait": cmd_wait, "done": cmd_done, "list": cmd_list, "orphans": cmd_orphans,
                "push": cmd_push, "pushed": cmd_pushed, "hubpage": cmd_hubpage, "setup": cmd_setup, "reply": cmd_reply,
                "channel-headers": cmd_channel_headers}[a.cmd](a)
    except KeyError as e:
        sys.exit(f"없는 작업: {e} — board list 로 확인")


if __name__ == "__main__":
    sys.exit(main())
