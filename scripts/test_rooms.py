"""Offline chat-selection and leave checks; no Win32 instance or live input."""
import contextlib
from dataclasses import replace
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kakaoctl import Controller, KakaoError, Room, Window
from kakao_rooms import RoomActions
from test_kakaoctl import FakeApp


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class RoomApi(FakeApp):
    def __init__(self):
        super().__init__()
        self.main = 10
        self.view = Window(20, 7, "EVA_Window", "ChatRoomListView_test", 10)
        self.list = Window(21, 7, "EVA_VH_ListControl_Dblclk", "ChatRoomListCtrl_test", 20)
        self.foreground = self.main
        self.selected_hwnd = 100
        self.focused = []
        self.keys = []
        self.events = []
        self.popups = []
        self.membership = True
        self.searches = []
        self.modifier = False
        self.delete_error = False
        self.popup_mode = "normal"
        self.confirm_closes = True
        self.reject_enter = False
        self.reject_release = False
        self.focus_failure = None
        self.verification_error = None
        self.drift_on_snapshot = False
        self.gui = SimpleNamespace(GetForegroundWindow=lambda: self.foreground,
                                   IsWindowEnabled=lambda h: not (self.popup_mode == "disabled" and h == 300))
        self.user = SimpleNamespace(GetAsyncKeyState=lambda k: 0x8000 if self.modifier else 0)

    def top_windows(self):
        if self.drift_on_snapshot and self.foreground == 100:
            self.foreground = 999
        return [Window(10, 7, "EVA_Window_Dblclk", "카카오톡")] + [r.window for r in self.rooms] + self.popups

    def children(self, hwnd):
        if hwnd == self.main:
            return [self.view, self.list]
        if hwnd == self.view.hwnd:
            return [self.list]
        if any(w.hwnd == hwnd for w in self.popups) and self.popup_mode == "children":
            return [Window(302, 7, "Edit", "", hwnd)]
        return []

    def window(self, hwnd):
        for w in self.top_windows() + [self.view, self.list]:
            if w.hwnd == hwnd:
                return replace(w, title="달라진 방") if self.changed and hwnd == 100 else w
        raise KakaoError("STALE_HANDLE", "gone")

    def valid(self, hwnd):
        return any(w.hwnd == hwnd for w in self.top_windows() + [self.view, self.list])

    def focus(self, hwnd):
        self.focused.append(hwnd)
        if hwnd == self.focus_failure:
            raise KakaoError("FOCUS_NOT_VERIFIED", "fake focus failure", dispatch_started=False)
        self.foreground = self.main if hwnd == self.list.hwnd else hwnd

    def key(self, hwnd, key, ctrl=False):
        self.keys.append((hwnd, key))
        if hwnd == self.list.hwnd and key == 13:
            self.foreground = self.selected_hwnd
        elif hwnd == self.list.hwnd and key == 0x2E:
            if self.delete_error:
                raise KakaoError("WINDOW_TIMEOUT", "posting may have started")
            if self.popup_mode == "missing":
                return
            parent = 100 if self.popup_mode == "wrong_owner" else self.main
            title = "unrelated dialog" if self.popup_mode == "caption" else ""
            class_name = "OtherClass" if self.popup_mode == "class" else "EVA_Window_Dblclk"
            self.popups = [Window(300, 7, class_name, title, parent)]
            if self.popup_mode == "multiple":
                self.popups.append(Window(301, 7, "EVA_Window_Dblclk", "", parent))
            self.foreground = self.main if self.popup_mode == "inactive" else 300

    def _native_key_event(self, key, up=False):
        self.events.append((key, up))
        if not up:
            if self.reject_enter:
                return False
            if self.confirm_closes:
                self.rooms = []
                self.popups = []
                self.foreground = self.main
                if self.popup_mode != "still_member":
                    self.membership = False
        return not (up and self.reject_release)


class RoomController(Controller):
    def resolve(self, title, hwnd=None, *, allow_search=True):
        if hwnd is None and allow_search and not self.candidates(title):
            self.api.searches.append(title)
            if self.api.verification_error:
                raise KakaoError(self.api.verification_error, "search could not run")
            if self.api.membership:
                r = Room(Window(200, 7, "EVA_Window_Dblclk", title), 201, 202)
                self.api.rooms.append(r)
                return r
            raise KakaoError("ROOM_NOT_FOUND", "no local exact match")
        return super().resolve(title, hwnd, allow_search=allow_search)


class RoomTests(unittest.TestCase):
    def setUp(self):
        self.api = RoomApi()
        self.app = RoomController(self.api)
        self.actions = RoomActions(self.app)
        self.clock = Clock()
        self.addCleanup(patch.stopall)
        patch("kakao_rooms.time", self.clock).start()

    def leave(self, **kwargs):
        return self.actions.leave("테스트 방", 100, **kwargs)

    def test_plan_does_not_focus_open_search_or_delete(self):
        result = self.leave()
        self.assertEqual(result["status"], "planned")
        self.assertEqual(self.api.focused + self.api.keys + self.api.events + self.api.searches, [])

    def test_missing_handle_stops_before_selection(self):
        with self.assertRaises(KakaoError) as error:
            self.actions.leave("테스트 방", None, execute=True)
        self.assertEqual(error.exception.code, "ROOM_HANDLE_REQUIRED")
        self.assertEqual(self.api.keys, [])

    def test_stale_handle_never_searches_or_deletes(self):
        with self.assertRaises(KakaoError):
            self.actions.leave("테스트 방", 999, execute=True)
        self.assertEqual(self.api.keys + self.api.searches, [])

    def test_selected_returns_metadata_without_copy_or_send(self):
        self.api.copy_history = lambda h: self.fail("must not copy history")
        result = self.actions.selected()
        self.assertEqual(result["room"]["hwnd"], 100)
        self.assertFalse(result["history_read"])
        self.assertEqual(result["room_type"], "not_classified")
        self.assertEqual(self.api.keys, [(21, 13)])
        self.assertEqual(self.api.events, [])

    def test_hidden_chat_tab_blocks_input(self):
        self.api.view = replace(self.api.view, visible=False)
        with self.assertRaises(KakaoError) as error:
            self.actions.selected()
        self.assertEqual(error.exception.code, "CHAT_TAB_HIDDEN")
        self.assertEqual(self.api.keys, [])

    def test_unidentified_selection_does_not_delete(self):
        self.api.selected_hwnd = self.api.main
        with self.assertRaises(KakaoError) as error:
            self.leave(execute=True)
        self.assertEqual(error.exception.code, "SELECTED_ROOM_NOT_VERIFIED")
        self.assertNotIn((21, 0x2E), self.api.keys)

    def test_same_title_different_handle_does_not_delete(self):
        self.api.rooms.append(Room(replace(self.api.room.window, hwnd=200), 201, 202))
        self.api.selected_hwnd = 200
        with self.assertRaises(KakaoError) as error:
            self.leave(execute=True)
        self.assertEqual(error.exception.code, "LEAVE_SELECTION_MISMATCH")
        self.assertNotIn((21, 0x2E), self.api.keys)

    def test_focus_drift_after_selection_blocks_delete(self):
        self.api.drift_on_snapshot = True
        with self.assertRaises(KakaoError):
            self.leave(execute=True)
        self.assertNotIn((21, 0x2E), self.api.keys)

    def test_physical_modifier_blocks_all_input(self):
        self.api.modifier = True
        with self.assertRaises(KakaoError) as error:
            self.leave(execute=True)
        self.assertEqual(error.exception.code, "MODIFIER_HELD")
        self.assertEqual(self.api.keys + self.api.events, [])

    def test_success_requires_delete_confirmation_and_search_absence(self):
        result = self.leave(execute=True)
        self.assertEqual(result["status"], "left")
        self.assertEqual(result["verification"], "window_closed_and_local_search_absent")
        self.assertFalse(result["server_membership_verified"])
        self.assertEqual(self.api.keys, [(21, 13), (21, 0x2E)])
        self.assertEqual(self.api.events, [(13, False), (13, True)])
        self.assertGreaterEqual(self.clock.now, 0.15)
        self.assertEqual(self.api.searches, ["테스트 방"])
        self.assertEqual(self.api.writes, [])

    def test_missing_ambiguous_or_changed_dialogs_never_receive_enter(self):
        for mode in ("missing", "multiple", "children", "caption", "wrong_owner", "inactive", "disabled", "class"):
            with self.subTest(mode=mode):
                api = RoomApi()
                api.popup_mode = mode
                result = RoomActions(RoomController(api)).leave("테스트 방", 100, execute=True)
                self.assertEqual(result["status"], "leave_outcome_unknown")
                self.assertEqual(api.keys.count((21, 0x2E)), 1)
                self.assertEqual(api.events, [])

    def test_partial_delete_failure_is_unknown_and_not_retried(self):
        self.api.delete_error = True
        result = self.leave(execute=True)
        self.assertEqual(result["status"], "leave_outcome_unknown")
        self.assertEqual(self.api.keys.count((21, 0x2E)), 1)
        self.assertEqual(self.api.events, [])

    def test_dialog_revalidation_blocks_changed_popup(self):
        original = self.actions.confirmation
        def changed_popup(before, timeout):
            popup = original(before, timeout)
            self.api.popups = [replace(popup, title="changed")]
            return popup
        self.actions.confirmation = changed_popup
        result = self.leave(execute=True)
        self.assertEqual(result["error_code"], "LEAVE_DIALOG_CHANGED")
        self.assertEqual(self.api.events, [])

    def test_confirmation_focus_failure_does_not_press_enter(self):
        self.api.focus_failure = 300
        result = self.leave(execute=True)
        self.assertEqual(result["status"], "leave_outcome_unknown")
        self.assertEqual(result["phase"], "confirmation_pending")
        self.assertEqual(self.api.events, [])

    def test_rejected_enter_does_not_retry(self):
        self.api.reject_enter = True
        result = self.leave(execute=True)
        self.assertEqual(result["status"], "leave_outcome_unknown")
        self.assertEqual(self.api.events, [(13, False)])

    def test_key_release_failure_attempts_release_but_never_second_keydown(self):
        self.api.reject_release = True
        result = self.leave(execute=True)
        self.assertEqual(result["status"], "leave_outcome_unknown")
        self.assertEqual(self.api.events.count((13, False)), 1)
        self.assertEqual(self.api.events.count((13, True)), 2)

    def test_unchanged_room_after_confirmation_is_unknown(self):
        self.api.confirm_closes = False
        result = self.leave(execute=True)
        self.assertEqual(result["error_code"], "LEAVE_CLOSE_NOT_VERIFIED")
        self.assertEqual(result["status"], "leave_outcome_unknown")
        self.assertEqual(self.api.events.count((13, False)), 1)

    def test_window_close_alone_does_not_prove_leaving(self):
        self.api.popup_mode = "still_member"
        result = self.leave(execute=True)
        self.assertEqual(result["error_code"], "LEAVE_STILL_SEARCHABLE")
        self.assertEqual(result["status"], "leave_outcome_unknown")

    def test_search_failure_is_not_mistaken_for_absence(self):
        self.api.verification_error = "CHAT_TAB_HIDDEN"
        result = self.leave(execute=True)
        self.assertEqual(result["error_code"], "CHAT_TAB_HIDDEN")
        self.assertEqual(result["status"], "leave_outcome_unknown")

    def test_preexisting_popup_is_never_confirmed(self):
        self.api.popups = [Window(300, 7, "EVA_Window_Dblclk", "", self.api.main)]
        result = self.leave(execute=True)
        self.assertEqual(result["error_code"], "LEAVE_DIALOG_NOT_FOUND")
        self.assertEqual(self.api.events, [])

    def test_cli_unknown_leave_has_nonzero_exit(self):
        import kakaoctl
        api = SimpleNamespace(exclusive=lambda: contextlib.nullcontext())
        with patch("kakaoctl.Win32", return_value=api), patch("kakao_rooms.RoomActions") as actions, \
                patch("kakaoctl.sys.argv", ["kakaoctl.py", "leave", "--room", "테스트 방",
                                           "--room-hwnd", "100", "--execute"]), \
                patch("kakaoctl.sys.stdout", new_callable=io.StringIO) as output:
            actions.return_value.leave.return_value = {"status": "leave_outcome_unknown"}
            code = kakaoctl.main()
            result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "leave_outcome_unknown")


if __name__ == "__main__":
    unittest.main()
