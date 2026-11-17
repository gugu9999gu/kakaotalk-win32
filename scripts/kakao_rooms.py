"""Selected-chat discovery and explicitly requested leaving; no visual automation."""
from __future__ import annotations

import time

from kakaoctl import KakaoError, Room


class RoomActions:
    def __init__(self, app):
        self.app, self.api = app, app.api

    def list_control(self):
        self.api.ensure_ready()
        views = [w for w in self.api.children(self.api.main)
                 if w.visible and w.title.startswith("ChatRoomListView_")]
        if len(views) != 1:
            raise KakaoError("CHAT_TAB_HIDDEN", "Show the native chats tab before selecting or leaving a room.")
        lists = [w for w in self.api.children(views[0].hwnd)
                 if w.visible and w.parent == views[0].hwnd
                 and w.class_name.startswith("EVA_VH_ListControl")
                 and w.title.startswith(("ChatRoomListCtrl_", "SearchListCtrl_"))]
        if len(lists) != 1:
            raise KakaoError("CHAT_LIST_NOT_UNIQUE", "The visible native chat list is not uniquely identifiable.")
        return lists[0].hwnd

    def idle_keyboard(self):
        if any(self.api.user.GetAsyncKeyState(k) & 0x8000
               for k in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            raise KakaoError("MODIFIER_HELD", "Release physical modifiers before native chat-list input.",
                             dispatch_started=False)

    def selected_room(self, list_hwnd=None, timeout=2):
        """Open only the list's current selection, and return metadata, not history."""
        current = self.list_control()
        if list_hwnd is not None and current != list_hwnd:
            raise KakaoError("CHAT_LIST_CHANGED", "The visible chat list changed before selection.")
        before = {w.hwnd for w in self.api.top_windows()}
        self.idle_keyboard()
        self.api.focus(current)
        self.api.key(current, 0x0D)  # Enter goes to the list, never to a composer.
        deadline = time.monotonic() + timeout
        while True:
            foreground = self.api.gui.GetForegroundWindow()
            if self.api.valid(foreground):
                room = self.api.as_room(self.api.window(foreground))
                if room:
                    self.app.assert_room(room, room.window.title)
                    return Room(room.window, room.composer, room.history, room.window.hwnd not in before)
            if time.monotonic() >= deadline:
                raise KakaoError("SELECTED_ROOM_NOT_VERIFIED", "List Enter did not activate an identifiable native room.")
            time.sleep(0.05)

    def selected(self):
        room = self.selected_room()
        return {"status": "selected", "room": self.app.describe_room(room),
                "history_read": False, "room_type": "not_classified"}

    def confirmation(self, before, timeout):
        deadline = time.monotonic() + timeout
        while True:
            popups = [w for w in self.api.top_windows()
                      if w.visible and w.hwnd not in before and w.parent == self.api.main]
            if popups:
                if len(popups) != 1:
                    raise KakaoError("LEAVE_DIALOG_AMBIGUOUS", "More than one new owned window appeared.")
                popup = popups[0]
                if (popup.class_name != "EVA_Window_Dblclk" or popup.title
                        or self.api.children(popup.hwnd)
                        or not self.api.gui.IsWindowEnabled(popup.hwnd)
                        or self.api.gui.GetForegroundWindow() != popup.hwnd):
                    raise KakaoError("LEAVE_DIALOG_CHANGED", "The new window is not the observed leave-confirmation shape.")
                return popup
            if time.monotonic() >= deadline:
                raise KakaoError("LEAVE_DIALOG_NOT_FOUND", "No new identifiable leave confirmation appeared.")
            time.sleep(0.05)

    def confirm(self, popup):
        current = self.api.window(popup.hwnd)
        if (current != popup or self.api.children(popup.hwnd)
                or not self.api.gui.IsWindowEnabled(popup.hwnd)
                or self.api.gui.GetForegroundWindow() != popup.hwnd):
            raise KakaoError("LEAVE_DIALOG_CHANGED", "The bound leave confirmation changed before Enter.")
        self.idle_keyboard()
        self.api.focus(popup.hwnd)
        if self.api.gui.GetForegroundWindow() != popup.hwnd:
            raise KakaoError("FOREGROUND_NOT_VERIFIED", "The leave confirmation is not active.")
        self.idle_keyboard()
        pressed = False
        try:
            if not self.api._native_key_event(0x0D):
                raise KakaoError("NATIVE_KEY_FAILED", "Leave-confirmation Enter was rejected.")
            pressed = True
            time.sleep(0.15)
            if not self.api._native_key_event(0x0D, up=True):
                raise KakaoError("NATIVE_KEY_FAILED", "Leave-confirmation Enter release failed.")
            pressed = False
        finally:
            if pressed:
                self.api._native_key_event(0x0D, up=True)

    def verify_absence(self, title):
        try:
            self.app.resolve(title)  # Existing local chats only; never a public-room join link.
        except KakaoError as exc:
            if exc.code == "ROOM_NOT_FOUND":
                return True
            raise
        return False

    def leave(self, title, hwnd, *, execute=False, timeout=3):
        if hwnd is None:
            raise KakaoError("ROOM_HANDLE_REQUIRED", "Leaving requires the verified current --room-hwnd.")
        if timeout <= 0:
            raise KakaoError("INVALID_TIMEOUT", "Timeout must be positive.")
        room = self.app.resolve(title, hwnd, allow_search=False)
        result = {"status": "planned", "room": self.app.describe_room(room),
                  "execute_required": True, "phase": "planned",
                  "effect": "Leave the selected chat; the client removes its local chat history.",
                  "server_membership_verified": False}
        if not execute:
            return result  # No focus changes, search, key presses, or confirmations.

        target_list = self.list_control()
        selected = self.selected_room(target_list)
        if ((selected.window.hwnd, selected.composer, selected.history)
                != (room.window.hwnd, room.composer, room.history)):
            raise KakaoError("LEAVE_SELECTION_MISMATCH", "The selected list item is not the bound room; no Delete issued.",
                             selected_room=self.app.describe_room(selected))
        self.app.assert_room(room, title)
        before = {w.hwnd for w in self.api.top_windows()}
        if self.api.gui.GetForegroundWindow() != room.window.hwnd:
            raise KakaoError("LEAVE_SELECTION_MISMATCH", "Focus changed after selection verification; no Delete issued.")
        self.api.focus(target_list)
        self.idle_keyboard()
        if self.list_control() != target_list or self.api.gui.GetForegroundWindow() != self.api.main:
            raise KakaoError("CHAT_LIST_CHANGED", "The verified chat list is no longer active; no Delete issued.")
        self.app.assert_room(room, title)
        result.update(execute_required=False, phase="delete_dispatched")
        try:
            # Record the phase before posting: a partial key-post failure is an unknown outcome.
            self.api.key(target_list, 0x2E)
            popup = self.confirmation(before, timeout)
            result.update(phase="confirmation_pending", confirmation_hwnd=popup.hwnd)
            self.app.assert_room(room, title)
            self.confirm(popup)
            result["phase"] = "confirmation_dispatched"
            deadline = time.monotonic() + timeout
            while self.api.valid(room.window.hwnd) or self.api.valid(popup.hwnd):
                if time.monotonic() >= deadline:
                    raise KakaoError("LEAVE_CLOSE_NOT_VERIFIED", "The room or its confirmation is still present.")
                time.sleep(0.05)
            result["phase"] = "checking_local_search"
            if not self.verify_absence(title):
                raise KakaoError("LEAVE_STILL_SEARCHABLE", "An exact-title local room was found after confirmation.")
            result.update(status="left", phase="verified", verification="window_closed_and_local_search_absent")
            return result
        except Exception as exc:
            result.update(status="leave_outcome_unknown", error_code=getattr(exc, "code", type(exc).__name__),
                          error=str(exc), message="Leaving may already have happened. Inspect the current client; do not retry automatically.")
            return result
