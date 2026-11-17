"""Focused behavior checks. Every app/clipboard used here is an in-memory fake."""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kakaoctl import ClipboardSnapshot, Controller, KakaoError, Room, Window, Win32, parse_history


class FakeApp:
    def __init__(self):
        self.pid = 7
        self.room = Room(Window(100, 7, "EVA_Window_Dblclk", "테스트 방"), 101, 102)
        self.rooms = [self.room]
        self.draft = ""
        self.writes = []
        self.dispatches = []
        self.send_error = False
        self.changed = False
        self.closed = []
        self.history = "2026년 11월 17일 화요일\r\n[가] [오후 1:00] 앞 내용\r\n[나] [오후 1:01] 최신\n둘째 줄\r\n"

    def ensure_ready(self):
        pass

    def top_windows(self):
        return [r.window for r in self.rooms]

    def window(self, hwnd):
        for r in self.rooms:
            if r.window.hwnd == hwnd:
                return replace(r.window, title="다른 방") if self.changed else r.window
        raise KakaoError("STALE_HANDLE", "gone")

    def as_room(self, window):
        return next((r for r in self.rooms if r.window.hwnd == window.hwnd), None)

    def get_text(self, hwnd):
        return self.draft

    def draft_text(self, hwnd):
        return self.get_text(hwnd)

    def set_text(self, hwnd, text):
        self.writes.append((hwnd, text))
        self.draft = text

    def key(self, hwnd, key, ctrl=False):
        self.dispatches.append((hwnd, key, ctrl))
        if self.send_error:
            raise RuntimeError("unknown after dispatch")
        self.draft = ""

    def send_key(self, hwnd, ctrl=False):
        return self.key(hwnd, 13, ctrl)

    def copy_history(self, hwnd):
        return self.history, {"clipboard_restored": True, "clipboard_restore_skipped": False}

    def close(self, hwnd):
        self.closed.append(hwnd)


class AppTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeApp()
        self.app = Controller(self.api)

    def test_draft_writes_unicode_without_send(self):
        text = '한글 😀\n"견적" $계산'
        result = self.app.write("테스트 방", text)
        self.assertEqual(result["status"], "drafted")
        self.assertEqual(self.api.draft, text)
        self.assertEqual(self.api.dispatches, [])

    def test_existing_draft_is_preserved(self):
        self.api.draft = "사용자 초안"
        with self.assertRaises(KakaoError) as context:
            self.app.write("테스트 방", "바꿀 내용")
        self.assertEqual(context.exception.code, "DRAFT_EXISTS")
        self.assertEqual(self.api.draft, "사용자 초안")
        self.assertEqual(self.api.writes, [])

    def test_authorized_replace(self):
        self.api.draft = "이전 초안"
        self.app.write("테스트 방", "교체", replace_draft=True)
        self.assertEqual(self.api.draft, "교체")

    def test_append_keeps_existing_draft(self):
        self.api.draft = "첫 줄"
        self.app.write("테스트 방", "\n둘째 줄", append=True)
        self.assertEqual(self.api.draft, "첫 줄\n둘째 줄")

    def test_send_defaults_to_plan_no_composer_change(self):
        result = self.app.write("테스트 방", "보낼 내용", mode="send")
        self.assertEqual(result["status"], "planned")
        self.assertTrue(result["execute_required"])
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.api.dispatches, [])

    def test_execute_dispatches_once_and_does_not_claim_delivery(self):
        result = self.app.write("테스트 방", "보낼 내용", hwnd=100, mode="send", execute=True)
        self.assertEqual(result["status"], "submitted")
        self.assertFalse(result["delivery_confirmed"])
        self.assertEqual(self.api.dispatches, [(101, 13, False)])

    def test_ctrl_enter_is_explicit(self):
        self.app.write("테스트 방", "내용", hwnd=100, mode="send", execute=True, send_key="ctrl-enter")
        self.assertEqual(self.api.dispatches, [(101, 13, True)])

    def test_unknown_send_never_retries(self):
        self.api.send_error = True
        with self.assertRaises(KakaoError) as context:
            self.app.write("테스트 방", "내용", hwnd=100, mode="send", execute=True)
        self.assertEqual(context.exception.code, "SEND_OUTCOME_UNKNOWN")
        self.assertEqual(len(self.api.dispatches), 1)

    def test_changed_title_stops_before_write(self):
        self.api.changed = True
        with self.assertRaises(KakaoError) as context:
            self.app.write("테스트 방", "내용", hwnd=100, mode="send", execute=True)
        self.assertEqual(context.exception.code, "ROOM_CHANGED")
        self.assertEqual(self.api.writes, [])

    def test_duplicate_titles_are_ambiguous(self):
        self.api.rooms.append(Room(replace(self.api.room.window, hwnd=200), 201, 202))
        with self.assertRaises(KakaoError) as context:
            self.app.write("테스트 방", "내용")
        self.assertEqual(context.exception.code, "AMBIGUOUS_ROOM")
        self.assertEqual(self.api.writes, [])

    def test_handle_disambiguates_open_rooms(self):
        self.api.rooms.append(Room(replace(self.api.room.window, hwnd=200), 201, 202))
        room = self.app.resolve("테스트 방", 200)
        self.assertEqual(room.composer, 201)

    def test_wrong_handle_never_searches(self):
        with self.assertRaises(KakaoError) as context:
            self.app.resolve("테스트 방", 777)
        self.assertEqual(context.exception.code, "ROOM_HANDLE_MISMATCH")

    def test_invalid_text_stops_before_app_access(self):
        for text in ("", "\t ", "앞\0뒤"):
            with self.assertRaises(KakaoError) as context:
                self.app.write("테스트 방", text)
            self.assertEqual(context.exception.code, "INVALID_TEXT")
        self.assertEqual(self.api.writes, [])

    def test_actual_send_requires_explicit_current_room_handle(self):
        with self.assertRaises(KakaoError) as context:
            self.app.write("테스트 방", "내용", mode="send", execute=True)
        self.assertEqual(context.exception.code, "ROOM_HANDLE_REQUIRED")
        self.assertEqual(self.api.writes, [])
        self.assertEqual(self.api.dispatches, [])

    def test_writing_does_not_search_for_closed_room(self):
        self.api.rooms = []
        with self.assertRaises(KakaoError) as context:
            self.app.write("테스트 방", "내용")
        self.assertEqual(context.exception.code, "ROOM_NOT_OPEN")
        self.assertEqual(self.api.writes, [])

    def test_pre_dispatch_failure_keeps_known_error(self):
        def fail(*args, **kwargs):
            raise KakaoError("KEYBOARD_ATTACH_FAILED", "not dispatched", dispatch_started=False)
        self.api.key = fail
        with self.assertRaises(KakaoError) as context:
            self.app.write("테스트 방", "내용", hwnd=100, mode="send", execute=True)
        self.assertEqual(context.exception.code, "KEYBOARD_ATTACH_FAILED")
        self.assertFalse(context.exception.details["dispatch_started"])

    def test_main_shape_is_not_just_the_window_title(self):
        native = object.__new__(Win32)
        titles = {11: "OnlineMainView_0x123", 101: ""}
        def enum_children(parent, callback, parameter):
            callback({10: 11, 100: 101}[parent], parameter)
        native.gui = SimpleNamespace(EnumChildWindows=enum_children, GetWindowText=lambda h: titles[h])
        self.assertTrue(native._is_main_window(10))
        self.assertFalse(native._is_main_window(100))

    def test_native_focus_attaches_and_verifies_the_composer(self):
        native = object.__new__(Win32)
        calls = []
        focused = [0]
        native.window = lambda h: Window(h, 7, "RICHEDIT50W", "", 100)
        native.kernel = SimpleNamespace(GetCurrentThreadId=lambda: 10)
        native.proc = SimpleNamespace(GetWindowThreadProcessId=lambda h: (20, 7))
        native.user = SimpleNamespace(AttachThreadInput=lambda a,b,on: calls.append(on) or True)
        native.gui = SimpleNamespace(PeekMessage=lambda *a: None,
            GetParent=lambda h: 100 if h == 101 else 0,
            SetActiveWindow=lambda h: calls.append(h),
            SetFocus=lambda h: focused.__setitem__(0, h), GetFocus=lambda: focused[0])
        native.focus(101)
        self.assertEqual(focused[0], 101)
        self.assertEqual(calls, [True, 100, False])

    def test_native_send_holds_one_enter_stroke(self):
        native = object.__new__(Win32)
        events = []
        native.window = lambda h: Window(h, 7, "RICHEDIT50W", "", 100)
        native.focus = lambda h: None
        native.user = SimpleNamespace(GetAsyncKeyState=lambda k: 0)
        native._native_key_event = lambda key, up=False: events.append((key, up)) or True
        with patch("kakaoctl.time.sleep") as sleep:
            native.send_key(101)
        self.assertEqual(events, [(13, False), (13, True)])
        sleep.assert_called_once_with(0.15)

    def test_empty_editor_hint_is_not_a_user_draft(self):
        native = object.__new__(Win32)
        native.get_text = lambda h: "메시지 입력"
        native.window = lambda h: Window(h, 7, "RICHEDIT50W", "", 100)
        self.assertEqual(native.draft_text(101), "")

    def test_multiline_parser_retains_dates(self):
        messages = parse_history(self.api.history)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["text"], "최신\n둘째 줄")
        self.assertEqual(messages[1]["date"], "2026년 11월 17일 화요일")

    def test_read_last_message_and_preserve_already_open_room(self):
        result = self.app.read("테스트 방", limit=1)
        self.assertEqual(result["copied_message_count"], 2)
        self.assertEqual(result["messages"][0]["sender"], "나")
        self.assertEqual(self.api.closed, [])

    def test_large_message_bounded_in_both_representations(self):
        self.api.history += "[나] [오후 1:02] " + "한" * 10000
        result = self.app.read("테스트 방", max_chars=100)
        self.assertLessEqual(len(result["text"]), 100)
        self.assertLessEqual(sum(len(m["text"]) for m in result["messages"]), 100)
        self.assertTrue(result["text_truncated"])
        self.assertTrue(result["truncated_first_message"])


class FakeClipboard:
    def __init__(self, initial=None):
        self.pid = 7
        self.data = {}
        self.allocations = {}
        self.counter = 1000
        self.sequence = 1
        self.owner_pid = 99
        self.freed = []
        self.destroyed = []
        self.fail_duplicate = False
        self.fail_set = False
        self.clip = SimpleNamespace(EnumClipboardFormats=self.enum, EmptyClipboard=self.empty,
            GetClipboardSequenceNumber=lambda: self.sequence, GetClipboardOwner=lambda: self.owner_pid,
            IsClipboardFormatAvailable=lambda fmt: fmt in self.data,
            GetClipboardData=lambda fmt: self.allocations[self.data[fmt]])
        self.user = SimpleNamespace(GetClipboardData=lambda fmt: self.data[fmt], SetClipboardData=self.set_data)
        self.ole = SimpleNamespace(OleDuplicateData=self.duplicate)
        self.gui = SimpleNamespace(CreateWindowEx=lambda *args: 800,
                                   DestroyWindow=lambda hwnd: self.destroyed.append(hwnd))
        self.proc = SimpleNamespace(GetWindowThreadProcessId=lambda hwnd: (1, hwnd))
        for fmt, value in (initial or {}).items():
            self.data[fmt] = self.allocate(value)

    def allocate(self, value):
        self.counter += 1
        self.allocations[self.counter] = value
        return self.counter

    def enum(self, previous):
        formats = list(self.data)
        return formats[0] if not previous and formats else (
            formats[formats.index(previous) + 1] if previous in formats and formats.index(previous) + 1 < len(formats) else 0)

    def empty(self):
        self.data = {}
        self.sequence += 1

    def duplicate(self, handle, fmt, flags):
        return 0 if self.fail_duplicate else self.allocate(self.allocations[handle])

    def set_data(self, fmt, handle):
        if self.fail_set:
            return 0
        self.data[fmt] = handle
        self.sequence += 1
        return handle

    @contextmanager
    def open_clipboard(self, owner):
        yield

    def free_clipboard_handle(self, fmt, handle):
        self.freed.append((fmt, handle))

    def duplicate_clipboard_handle(self, fmt, handle):
        return self.duplicate(handle, fmt, 0)

    def replace_content(self, content, source):
        self.data = {13: self.allocate(content)}
        self.owner_pid = source
        self.sequence += 1

    def values(self):
        return {fmt: self.allocations[h] for fmt, h in self.data.items()}


class ClipboardTests(unittest.TestCase):
    def test_text_html_and_image_are_restored(self):
        original = {13: "원래 한글 😀", 49160: b"<b>HTML</b>", 8: b"DIB-image-data"}
        api = FakeClipboard(original)
        with ClipboardSnapshot(api) as snapshot:
            api.replace_content("새 카톡 대화", source=7)
            self.assertEqual(snapshot.capture(), "새 카톡 대화")
        self.assertEqual(api.values(), original)
        self.assertTrue(snapshot.restored)
        self.assertEqual(api.freed, [])  # Transferred handles belong to Windows.

    def test_unsupported_format_does_not_empty_original(self):
        api = FakeClipboard({13: "유지", 0x200: b"private"})
        with self.assertRaises(KakaoError) as context:
            with ClipboardSnapshot(api):
                self.fail("Should not enter")
        self.assertEqual(context.exception.code, "CLIPBOARD_UNSUPPORTED")
        self.assertEqual(api.values(), {13: "유지", 0x200: b"private"})
        self.assertEqual(api.sequence, 1)

    def test_failed_duplicate_leaves_original(self):
        api = FakeClipboard({13: "유지"})
        api.fail_duplicate = True
        with self.assertRaises(KakaoError):
            with ClipboardSnapshot(api):
                pass
        self.assertEqual(api.values(), {13: "유지"})

    def test_new_external_clipboard_is_not_overwritten(self):
        api = FakeClipboard({13: "이전"})
        with ClipboardSnapshot(api) as snapshot:
            api.replace_content("카톡 복사", source=7)
            snapshot.capture()
            api.replace_content("사용자가 다른 앱에서 복사", source=99)
        self.assertFalse(snapshot.restored)
        self.assertEqual(api.values(), {13: "사용자가 다른 앱에서 복사"})
        self.assertEqual(len(api.freed), 1)

    def test_external_copy_during_capture_is_not_read(self):
        api = FakeClipboard({13: "이전"})
        with self.assertRaises(KakaoError) as context:
            with ClipboardSnapshot(api) as snapshot:
                api.replace_content("다른 앱의 민감 정보", source=99)
                snapshot.capture()
        self.assertEqual(context.exception.code, "CLIPBOARD_CHANGED")
        self.assertEqual(api.values(), {13: "다른 앱의 민감 정보"})

    def test_capture_pumps_clipboard_owner_notifications(self):
        api = FakeClipboard({13: "원본"})
        api.gui.PumpWaitingMessages = lambda: api.replace_content("지연된 복사", source=7)
        with ClipboardSnapshot(api) as snapshot:
            self.assertEqual(snapshot.capture(), "지연된 복사")
        self.assertEqual(api.values(), {13: "원본"})

    def test_copy_failure_restores_original(self):
        api = FakeClipboard({13: "원본"})
        with self.assertRaises(RuntimeError):
            with ClipboardSnapshot(api):
                raise RuntimeError("copy key failed")
        self.assertEqual(api.values(), {13: "원본"})

    def test_copy_failure_after_kakao_owns_clipboard_restores_original(self):
        api = FakeClipboard({13: "원본"})
        with self.assertRaises(RuntimeError):
            with ClipboardSnapshot(api):
                api.replace_content("복사된 내역", source=7)
                raise RuntimeError("failed before capture")
        self.assertEqual(api.values(), {13: "원본"})

    def test_restoration_failure_is_reported(self):
        api = FakeClipboard({13: "원본"})
        with self.assertRaises(KakaoError) as context:
            with ClipboardSnapshot(api) as snapshot:
                api.replace_content("대화", source=7)
                snapshot.capture()
                api.fail_set = True
        self.assertEqual(context.exception.code, "CLIPBOARD_RESTORE_FAILED")
        self.assertEqual(len(api.freed), 1)


if __name__ == "__main__":
    unittest.main()
