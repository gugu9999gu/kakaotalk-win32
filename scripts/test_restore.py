"""Test TalkCloud restore against fake windows and recognized menu labels."""
from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from kakao_restore import Label, RestoreActions, RestoreUI, unique_label
from kakaoctl import Controller, KakaoError, Window
from test_kakaoctl import FakeApp


class RestoreTests(TestCase):
    def setUp(self):
        self.app = Controller(FakeApp())
        self.room = self.app.api.room
        self.password = Window(200, 7, "EVA_Window_Dblclk", "", self.room.window.hwnd)
        self.result = Window(300, 7, "EVA_Window_Dblclk", "", self.room.window.hwnd)

    def test_plan_validates_room_without_opening_restore_menu(self):
        ui = Mock()
        action = RestoreActions(self.app, ui)
        self.assertEqual(action.restore(self.room.window.title, self.room.window.hwnd)["status"], "planned")
        self.assertEqual(ui.mock_calls, [])
        with self.assertRaises(KakaoError):
            action.restore(self.room.window.title, 999, execute=True)
        self.assertEqual(ui.mock_calls, [])

    def test_execute_reports_password_pending_and_returns_dialog_handle(self):
        ui = Mock()
        ui.open_dialog.return_value = self.password
        result = RestoreActions(self.app, ui).restore(self.room.window.title, self.room.window.hwnd, execute=True)
        self.assertEqual(result["status"], "awaiting_password")
        self.assertEqual(result["dialog_hwnd"], self.password.hwnd)
        ui.open_dialog.assert_called_once_with(self.room)

    def test_restore_label_requires_exact_unique_match(self):
        names = {"톡클라우드대화복원"}
        labels = [Label("대화 내용 모두 삭제", 30, 80), Label("톡클라우드 대화 복원", 30, 12)]
        self.assertEqual(unique_label(labels, names, "TEST"), labels[1])
        for unusable in (labels[:1], [labels[1], replace(labels[1], y=36)]):
            with self.assertRaises(KakaoError):
                unique_label(unusable, names, "TEST")

    def status_ui(self, windows, result_text):
        ui = RestoreUI(self.app)
        ui.api = SimpleNamespace(
            top_windows=lambda: windows,
            focus=Mock(),
        )
        ui.password_edit = lambda w: object() if w == self.password else None
        ui.labels = Mock(return_value=[Label(result_text, 30, 25)])
        return ui

    def test_pending_status_does_not_capture_password_dialog(self):
        ui = self.status_ui([self.password], "")
        result = RestoreActions(self.app, ui).status(self.room.window.title, self.room.window.hwnd,
                                                    self.password.hwnd)
        self.assertEqual(result["status"], "awaiting_password")
        self.assertFalse(result["completion_verified"])
        ui.labels.assert_not_called()

    def test_completion_requires_known_label_and_room_ownership(self):
        for windows, label, expected in (
            ([self.result], "대화 복원이 완료되었습니다.", True),
            ([replace(self.result, parent=999)], "대화 복원이 완료되었습니다.", False),
            ([self.result], "대화 복원이 완료되지 않았습니다.", False),
            ([], "대화 복원이 완료되었습니다.", False),
            ([self.result, replace(self.result, hwnd=400)], "대화 복원이 완료되었습니다.", False),
        ):
            with self.subTest(label=label, windows=windows):
                ui = self.status_ui(windows, label)
                output = RestoreActions(self.app, ui).status(self.room.window.title, self.room.window.hwnd,
                                                            self.password.hwnd)
                self.assertEqual(output["completion_verified"], expected)
                self.assertEqual(output["status"], "restored" if expected else "restore_outcome_unknown")
                if expected:
                    ui.labels.assert_called_once_with(self.result, header_only=True)
