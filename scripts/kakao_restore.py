"""Open and observe KakaoTalk's room-specific TalkCloud restore UI."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import time

from kakaoctl import KakaoError


@dataclass(frozen=True)
class Label:
    text: str
    x: int
    y: int


def normalized_label(text):
    return re.sub(r"[^\w]", "", text).casefold()


def unique_label(labels, names, code):
    matches = [label for label in labels if normalized_label(label.text) in names]
    if len(matches) != 1:
        raise KakaoError(code, "The expected Korean KakaoTalk label was not found uniquely.")
    return matches[0]


class RestoreUI:
    def __init__(self, app):
        self.app, self.api = app, app.api

    def owned(self, room):
        root = room.window.hwnd
        return [w for w in self.api.top_windows() if w.visible and w.parent == root]

    def scale(self, hwnd):
        dpi_for_window = self.api.user.GetDpiForWindow
        dpi_for_window.argtypes, dpi_for_window.restype = [wintypes.HWND], wintypes.UINT
        return (dpi_for_window(hwnd) or 96) / 96

    def hit(self, window, point):
        hit = self.api.gui.WindowFromPoint(point)
        return bool(self.api.valid(hit) and self.api.gui.GetAncestor(hit, 2) == window.hwnd)

    def labels(self, window, *, header_only=False):
        """Read menu labels or the top of an owned dialog, above any password field."""
        if self.api.window(window.hwnd) != window or not window.visible:
            raise KakaoError("RESTORE_WINDOW_CHANGED", "The expected menu or dialog changed.")
        if window.class_name not in ("EVA_Menu", "EVA_Window_Dblclk"):
            raise KakaoError("RESTORE_WINDOW_CHANGED", "Unexpected window for restore labels.")
        left, top, right, bottom = self.api.gui.GetWindowRect(window.hwnd)
        if header_only:
            bottom = min(bottom, top + round(90 * self.scale(window.hwnd)))
        if not (24 <= right - left <= 900 and 24 <= bottom - top <= 900):
            raise KakaoError("RESTORE_WINDOW_CHANGED", "The menu or dialog has unexpected dimensions.")
        for x in (left + 10, (left + right) // 2, right - 10):
            for y in (top + 10, (top + bottom) // 2, bottom - 10):
                if not self.hit(window, (x, y)):
                    raise KakaoError("RESTORE_WINDOW_OBSCURED", "The restore menu or dialog is covered.")

        winps = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        helper = Path(__file__).with_name("kakao_restore_labels.ps1")
        command = [str(winps), "-NoProfile", "-NonInteractive", "-File", str(helper),
                   "-Left", str(left), "-Top", str(top), "-Width", str(right - left), "-Height", str(bottom - top)]
        try:
            result = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=12,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode != 0:
                raise KakaoError("RESTORE_LABEL_READER_FAILED",
                                 "Windows PowerShell 5.1 and Korean Windows OCR must be available.")
            return [Label(**data) for data in json.loads(result.stdout)]
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
            raise KakaoError("RESTORE_LABEL_READER_FAILED", "Windows OCR did not return usable labels.") from exc

    def require_target(self, window, point):
        if self.api.window(window.hwnd) != window or not self.hit(window, point):
            raise KakaoError("RESTORE_TARGET_CHANGED", "The expected restore target is no longer at that point.")

    def label_point(self, menu, label):
        left, top, right, bottom = self.api.gui.GetWindowRect(menu.hwnd)
        point = (left + label.x, top + label.y)
        if not (left < point[0] < right and top < point[1] < bottom):
            raise KakaoError("RESTORE_LABEL_OUTSIDE_WINDOW", "The recognized menu label is out of bounds.")
        return point

    def move_pointer(self, window, point):
        self.require_target(window, point)
        if not self.api.user.SetCursorPos(*point):
            raise KakaoError("RESTORE_POINTER_FAILED", "Could not position the pointer at the verified label.")
        time.sleep(0.25)
        actual = self.api.gui.GetCursorPos()
        # Windows may adjust the cursor a few pixels when the submenu appears.
        offset = round(5 * self.scale(window.hwnd))
        if any(abs(value - expected) > offset for value, expected in zip(actual, point)):
            raise KakaoError("RESTORE_POINTER_MOVED", "The pointer moved while opening the restore menu.")
        self.require_target(window, actual)

    def click_menu(self, menu, label):
        point = self.label_point(menu, label)
        self.move_pointer(menu, point)
        self.api.user.mouse_event(0x02, 0, 0, 0, 0)
        try:
            time.sleep(0.15)
        finally:
            self.api.user.mouse_event(0x04, 0, 0, 0, 0)

    def single_owned(self, room, condition, code, *, timeout=3):
        deadline = time.monotonic() + timeout
        while True:
            matches = [w for w in self.owned(room) if condition(w)]
            if len(matches) == 1:
                return matches[0]
            if matches or time.monotonic() >= deadline:
                raise KakaoError(code, "No unique matching popup appeared for the specified room.")
            time.sleep(0.08)

    def password_edit(self, dialog):
        fields = [w for w in self.api.children(dialog.hwnd)
                  if w.visible and w.parent == dialog.hwnd and w.class_name == "Edit"
                  and self.api.gui.GetWindowLong(w.hwnd, -16) & 0x20]
        return fields[0] if len(fields) == 1 else None

    def open_dialog(self, room):
        self.app.assert_room(room, room.window.title)
        if self.owned(room):
            raise KakaoError("RESTORE_EXISTING_POPUP", "Close existing popups for this room before starting restore.")
        root = room.window
        self.api.focus(room.composer)
        if self.api.gui.GetForegroundWindow() != root.hwnd:
            raise KakaoError("RESTORE_ROOM_NOT_ACTIVE", "The specified chat did not become active.")
        _, _, width, _ = self.api.gui.GetClientRect(root.hwnd)
        scale = self.scale(root.hwnd)
        x, y = width - round(21 * scale), round(57 * scale)
        point = self.api.gui.ClientToScreen(root.hwnd, (x, y))
        self.require_target(root, point)
        if self.api.gui.WindowFromPoint(point) != root.hwnd or point[1] >= self.api.gui.GetWindowRect(room.history)[1]:
            raise KakaoError("RESTORE_TOOLBAR_CHANGED", "The room toolbar target could not be verified.")
        mouse_position = (y << 16) | x
        cursor = self.api.gui.GetCursorPos()
        try:
            # This toolbar action opens a menu. Subsequent items require verified visible labels.
            for message, flags in ((0x200, 0), (0x201, 1), (0x202, 0)):
                self.api.message(root.hwnd, message, flags, mouse_position)
            main = self.single_owned(room, lambda w: w.class_name == "EVA_Menu", "RESTORE_MENU_NOT_FOUND")
            time.sleep(0.3)  # Let Kakao finish painting the custom menu.
            history_menu = unique_label(self.labels(main), {"대화내용"}, "RESTORE_HISTORY_MENU_NOT_FOUND")
            self.move_pointer(main, self.label_point(main, history_menu))
            submenu = self.single_owned(room, lambda w: w.class_name == "EVA_Menu" and w.hwnd != main.hwnd,
                                        "RESTORE_SUBMENU_NOT_FOUND")
            time.sleep(0.2)
            restore = unique_label(self.labels(submenu),
                                   {"톡클라우드대화복원", "특클라우드대화복원", "톡서랍대화복원"},
                                   "RESTORE_ACTION_NOT_FOUND")
            self.app.assert_room(room, root.title)
            if self.api.gui.GetForegroundWindow() != root.hwnd:
                raise KakaoError("RESTORE_ROOM_NOT_ACTIVE", "Focus changed before the restore action.")
            self.click_menu(submenu, restore)
            dialog = self.single_owned(room, lambda w: w.class_name == "EVA_Window_Dblclk", "RESTORE_DIALOG_NOT_FOUND")
            if not self.password_edit(dialog):
                raise KakaoError("RESTORE_PASSWORD_PROMPT_NOT_FOUND", "The room opened a different popup; inspect KakaoTalk.")
            time.sleep(0.2)
            unique_label(self.labels(dialog, header_only=True), {"대화내용복원", "대화복원"}, "RESTORE_DIALOG_NOT_VERIFIED")
            return dialog
        finally:
            self.api.user.SetCursorPos(*cursor)

    def observe(self, room, dialog_hwnd):
        owned = self.owned(room)
        original = next((w for w in owned if w.hwnd == dialog_hwnd), None)
        if original and self.password_edit(original):
            return "awaiting_password"
        candidates = [w for w in owned if w.class_name == "EVA_Window_Dblclk"
                      and w.hwnd != dialog_hwnd and not self.password_edit(w)]
        if len(candidates) == 1:
            try:
                completion = candidates[0]
                self.api.focus(completion.hwnd)
                labels = self.labels(completion, header_only=True)
            except KakaoError:
                return "restore_outcome_unknown"
            if any(normalized_label(label.text) == "대화복원이완료되었습니다" for label in labels):
                return "restored"
        return "restore_outcome_unknown"


class RestoreActions:
    def __init__(self, app, ui=None):
        self.app = app
        self.ui = ui if ui is not None else RestoreUI(app)

    def restore(self, title, hwnd, *, execute=False):
        if not hwnd:
            raise KakaoError("ROOM_HANDLE_REQUIRED", "Restore requires the verified current --room-hwnd.")
        room = self.app.resolve(title, hwnd, allow_search=False)
        info = self.app.describe_room(room)
        if not execute:
            return {"status": "planned", "room": info, "execute_required": True, "scope": "one_room"}
        dialog = self.ui.open_dialog(room)
        return {"status": "awaiting_password", "room": info, "dialog_hwnd": dialog.hwnd,
                "next_step": "Use KakaoTalk's on-screen keypad and press Restore. Keep the completion dialog open."}

    def status(self, title, hwnd, dialog_hwnd, *, wait=0):
        if not hwnd or not dialog_hwnd:
            raise KakaoError("RESTORE_HANDLE_REQUIRED", "Supply the verified room and password-dialog handles.")
        room = self.app.resolve(title, hwnd, allow_search=False)
        deadline = time.monotonic() + wait
        while True:
            self.app.assert_room(room, title)
            result = self.ui.observe(room, dialog_hwnd)
            if result == "restored" or time.monotonic() >= deadline:
                return {"status": result, "room": self.app.describe_room(room), "dialog_hwnd": dialog_hwnd,
                        "completion_verified": result == "restored"}
            time.sleep(0.3 if result == "awaiting_password" else 0.6)
