"""Offline forward tests; all windows, key queues, and application state are fake.

Run from the skill directory:
  python -B -X utf8 -m unittest discover -s scripts -p "test_*.py" -v

These tests cover room binding, search, modifier timing, and send outcomes.
This file never constructs a live Win32 backend or accesses a real clipboard.
"""

import ctypes
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))
import kakaoctl as k
from test_kakaoctl import FakeApp


TITLE = "테스트 방"
TEXT = "확인했습니다. 내일 오전에 공유하겠습니다."


def observed(case, **values):
    print(json.dumps({"case": case, **values}, ensure_ascii=False))


class Clock:
    def __init__(self):
        self.now = 0.0
        self.callbacks = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration
        for callback in self.callbacks:
            callback()


class SearchApp(FakeApp):
    """Search rows are substring matches; Enter opens the selected row only."""

    SEARCH = 50

    def __init__(self, rows):
        super().__init__()
        self.rooms = []
        self.rows = rows
        self.query = ""
        self.selected = -1
        self.search_keys = []
        self.sent = []
        self.histories = {}

    def search_edit(self):
        return self.SEARCH

    def valid(self, hwnd):
        return hwnd == self.SEARCH or any(r.window.hwnd == hwnd for r in self.rooms)

    def get_text(self, hwnd):
        return self.query if hwnd == self.SEARCH else super().get_text(hwnd)

    def set_text(self, hwnd, text):
        if hwnd == self.SEARCH:
            self.query = text
            self.selected = -1
        else:
            super().set_text(hwnd, text)

    def key(self, hwnd, key, ctrl=False):
        if hwnd == self.SEARCH:
            self.search_keys.append(key)
            if key == 0x28:
                self.selected = min(self.selected + 1, len(self.rows) - 1)
            elif key == 0x0D and 0 <= self.selected < len(self.rows):
                if self.rows[self.selected] not in self.rooms:
                    self.rooms.append(self.rows[self.selected])
            return
        self.sent.append({"composer": hwnd, "text": self.draft})
        super().key(hwnd, key, ctrl)

    def copy_history(self, hwnd):
        return self.histories.get(hwnd, self.history), {"clipboard_restored": True}

    def close(self, hwnd):
        super().close(hwnd)
        self.rooms = [r for r in self.rooms if r.window.hwnd != hwnd]


def room(hwnd, title=TITLE):
    return k.Room(k.Window(hwnd, 7, "EVA_Window_Dblclk", title), hwnd + 1, hwnd + 2)


class QueuedSendApp(FakeApp):
    """Run the real Win32.key body against a fake delayed message queue.

    Ctrl+Enter is the configured send shortcut. A regular Enter inserts a newline.
    Posted messages carry no keyboard-state snapshot; the handler reads current
    Ctrl state when the queue processes WM_KEYDOWN. No native API is called.
    """

    def __init__(self, clock, latency):
        super().__init__()
        self.clock, self.latency = clock, latency
        self.keyboard = bytearray(256)
        self.pending = []
        self.handled = []
        self.sent = []
        self.clock.callbacks.append(self.pump)
        native = object.__new__(k.Win32)
        native.window = self.native_window
        native.api = SimpleNamespace(MapVirtualKey=lambda *_: 0x1C)
        native.kernel = SimpleNamespace(GetCurrentThreadId=lambda: 10)
        native.proc = SimpleNamespace(GetWindowThreadProcessId=lambda _: (20, self.pid))
        native.gui = SimpleNamespace(
            PeekMessage=lambda *_: None,
            SetFocus=lambda *_: None,
            PostMessage=self.post,
        )
        native.user = SimpleNamespace(
            AttachThreadInput=lambda *_: True,
            GetKeyboardState=self.get_keyboard_state,
            SetKeyboardState=self.set_keyboard_state,
        )
        native.get_text = self.get_text
        native.focus = lambda hwnd: None
        self.native = native

    def native_window(self, hwnd):
        if hwnd == self.room.composer:
            return k.Window(hwnd, self.pid, "RICHEDIT50W", self.draft, self.room.window.hwnd)
        if hwnd == self.room.history:
            return k.Window(hwnd, self.pid, "EVA_VH_ListControl", "", self.room.window.hwnd)
        return self.window(hwnd)

    def get_keyboard_state(self, pointer):
        ctypes.memmove(pointer, bytes(self.keyboard), 256)
        return True

    def set_keyboard_state(self, pointer):
        self.keyboard[:] = ctypes.string_at(pointer, 256)
        return True

    def post(self, hwnd, msg, key, lparam):
        self.pending.append((self.clock.now + self.latency, hwnd, msg, key))

    def pump(self):
        ready = [event for event in self.pending if event[0] <= self.clock.now]
        self.pending = [event for event in self.pending if event[0] > self.clock.now]
        for _, hwnd, msg, key in ready:
            if msg == 0x0100 and key == 0x0D:
                ctrl = bool(self.keyboard[0x11] & 0x80)
                self.handled.append({"hwnd": hwnd, "ctrl": ctrl})
                if ctrl:
                    self.sent.append(self.draft)
                    self.draft = ""
                else:
                    self.draft += "\n"

    def get_text(self, hwnd):
        self.pump()
        return super().get_text(hwnd)

    def key(self, hwnd, key, ctrl=False):
        self.dispatches.append((hwnd, key, ctrl))
        k.Win32.key(self.native, hwnd, key, ctrl)


class FakeFunction:
    """ctypes function placeholder allowing signature assignment, never a DLL."""

    def __call__(self, *args):
        raise AssertionError("Unexpected fake DLL invocation")


class FakeDll:
    def __init__(self):
        self.functions = {}

    def __getattr__(self, name):
        return self.functions.setdefault(name, FakeFunction())


class ForwardTests(unittest.TestCase):
    def test_open_read_draft_send_with_pinned_handle(self):
        api = SearchApp([room(100)])
        clock = Clock()
        with patch.object(k, "time", clock):
            app = k.Controller(api)
            opened = app.resolve(TITLE)
            summary_source = app.read(TITLE, hwnd=opened.window.hwnd, limit=1)
            drafted = app.write(TITLE, TEXT, hwnd=opened.window.hwnd)
            result = app.write(TITLE, TEXT, hwnd=opened.window.hwnd, mode="send", execute=True)
        self.assertEqual(summary_source["messages"][-1]["text"], "최신\n둘째 줄")
        self.assertEqual(drafted["status"], "drafted")
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(api.sent, [{"composer": 101, "text": TEXT}])
        self.assertEqual(api.closed, [])
        self.assertFalse(result["delivery_confirmed"])

    def test_search_reaches_exact_room_after_a_partial_match(self):
        api = SearchApp([room(200, TITLE + " 공지"), room(100)])
        clock = Clock()
        result, error = None, None
        with patch.object(k, "time", clock):
            try:
                app = k.Controller(api)
                opened = app.resolve(TITLE)
                result = app.write(TITLE, TEXT, hwnd=opened.window.hwnd, mode="send", execute=True)
            except k.KakaoError as exc:
                error = exc.code
        observed("partial_match_first", error=error, opened=[r.window.title for r in api.rooms],
                 search_keys=api.search_keys, sends=api.sent)
        self.assertIsNone(error, "The exact requested room exists as the second search result")
        self.assertEqual(result["room"]["hwnd"], 100)

    def test_closed_duplicate_search_results_require_disambiguation(self):
        api = SearchApp([room(200), room(100)])
        clock = Clock()
        error, result = None, None
        with patch.object(k, "time", clock):
            try:
                result = k.Controller(api).write(TITLE, TEXT, mode="send", execute=True)
            except k.KakaoError as exc:
                error = exc.code
        observed("closed_duplicate_titles", error=error, result=result, sends=api.sent)
        self.assertEqual(error, "ROOM_HANDLE_REQUIRED", "Closed same-title search rows must not send to the first")
        self.assertEqual(api.sent, [])

    def test_read_then_send_does_not_switch_same_title_conversations(self):
        first, second = room(100), room(200)
        api = SearchApp([first, second])
        api.histories[first.history] = "2026년 11월 17일\n[A] [오후 1:00] A방 대화"
        api.histories[second.history] = "2026년 11월 17일\n[B] [오후 1:00] B방 대화"
        clock = Clock()
        with patch.object(k, "time", clock):
            app = k.Controller(api)
            read_result = app.read(TITLE)
            api.rows = [second, first]  # Search order changes between CLI calls.
            send_result = app.write(TITLE, TEXT, hwnd=read_result["room"]["hwnd"], mode="send", execute=True)
        observed("read_send_target_drift", read_room=read_result["room"],
                 read_messages=read_result["messages"], closed=api.closed,
                 send_room=send_result["room"], sends=api.sent)
        self.assertEqual(read_result["room"]["hwnd"], send_result["room"]["hwnd"],
                         "The summarize-and-send request must retain its destination")

    def test_ctrl_enter_with_fast_queue(self):
        clock = Clock()
        api = QueuedSendApp(clock, latency=0.02)
        with patch.object(k, "time", clock):
            result = k.Controller(api).write(TITLE, TEXT, hwnd=100, mode="send", execute=True, send_key="ctrl-enter")
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(api.sent, [TEXT])

    def test_ctrl_enter_survives_queue_delay(self):
        clock = Clock()
        api = QueuedSendApp(clock, latency=0.5)
        result, error = None, None
        with patch.object(k, "time", clock):
            try:
                result = k.Controller(api).write(TITLE, TEXT, hwnd=100, mode="send", execute=True, send_key="ctrl-enter")
            except k.KakaoError as exc:
                error = exc.code
        observed("delayed_ctrl_enter", error=error, result=result, posted=api.dispatches,
                 handled=api.handled, sends=api.sent, draft=api.draft)
        self.assertEqual(api.sent, [TEXT], "Ctrl must still be active when the queued key is handled")
        self.assertEqual(result["status"], "submitted")

    def test_same_pid_room_named_kakaotalk_is_not_a_second_main_window(self):
        windows = [k.Window(10, 7, "EVA_Window", "카카오톡"), room(100, "카카오톡").window]
        fake_modules = {name: SimpleNamespace() for name in
                        ("win32clipboard", "win32gui", "win32process", "win32api")}
        error = None
        # Win32.__init__ is exercised only with fake imports/DLLs/discovery/readiness.
        with patch.dict(sys.modules, fake_modules), \
                patch.object(k.ctypes, "WinDLL", side_effect=lambda *_args, **_kw: FakeDll(), create=True), \
                patch.object(k.Win32, "_top_all", return_value=windows), \
                patch.object(k.Win32, "_exe", return_value=r"C:\Fake\KakaoTalk.exe"), \
                patch.object(k.Win32, "_is_main_window", side_effect=lambda hwnd: hwnd == 10), \
                patch.object(k.Win32, "ensure_ready"):
            try:
                k.Win32(pid=7)
            except k.KakaoError as exc:
                error = exc.code
                observed("same_pid_room_named_kakaotalk", error=error, details=exc.details)
        self.assertIsNone(error, "A chat room with the app's display name is not another client")

    def test_pre_dispatch_failure_keeps_its_known_failure_code(self):
        clock = Clock()
        api = QueuedSendApp(clock, latency=0.02)
        api.native.user.AttachThreadInput = lambda *_: False
        error = None
        with patch.object(k, "time", clock):
            try:
                k.Controller(api).write(TITLE, TEXT, hwnd=100, mode="send", execute=True, send_key="ctrl-enter")
            except k.KakaoError as exc:
                error = exc.code
                observed("pre_dispatch_failure", error=error, details=exc.details,
                         pending=api.pending, sends=api.sent, draft=api.draft)
        self.assertEqual(error, "KEYBOARD_ATTACH_FAILED", "No key was posted; the outcome is known")
        self.assertEqual(api.pending, [])


if __name__ == "__main__":
    observed("tested_source", sha256=hashlib.sha256((PACKAGE / "scripts" / "kakaoctl.py").read_bytes()).hexdigest())
    unittest.main()
