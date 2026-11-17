#!/usr/bin/env python3
"""Native Windows KakaoTalk read/draft/send CLI. No screenshot or OCR dependency."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata


class KakaoError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


@dataclass(frozen=True)
class Window:
    hwnd: int
    pid: int
    class_name: str
    title: str
    parent: int = 0
    visible: bool = True


@dataclass(frozen=True)
class Room:
    window: Window
    composer: int
    history: int
    opened_here: bool = False


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("data", wt.DWORD),
                ("flags", wt.DWORD), ("time", wt.DWORD), ("extra", ctypes.c_size_t)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [("vk", wt.WORD), ("scan", wt.WORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("extra", ctypes.c_size_t)]


class InputPayload(ctypes.Union):
    _fields_ = [("mouse", MouseInput), ("keyboard", KeyboardInput)]


class NativeInput(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("payload", InputPayload)]


def same_title(a: str, b: str) -> bool:
    return unicodedata.normalize("NFC", a) == unicodedata.normalize("NFC", b)


def normalized_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_history(text: str) -> list[dict]:
    """Parse Kakao's copied Korean/English log; retain unparsed text separately."""
    day = None
    messages: list[dict] = []
    for line in normalized_text(text).split("\n"):
        if re.match(r"^\d{4}년\s*\d{1,2}월\s*\d{1,2}일", line) or re.match(
            r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),", line
        ):
            day = line.strip()
            continue
        match = re.match(r"^\[(.+?)\] \[([^\]]*\d{1,2}:\d{2}[^\]]*)\] ?(.*)$", line)
        if match:
            messages.append({"date": day, "sender": match[1], "time": match[2], "text": match[3]})
        elif messages:
            messages[-1]["text"] += "\n" + line
    for message in messages:
        message["text"] = message["text"].rstrip("\n")
    return messages


class Controller:
    """App operations; the injected backend also permits tests without live input."""

    def __init__(self, api):
        self.api = api

    def describe_room(self, room: Room) -> dict:
        return {"pid": room.window.pid, "hwnd": room.window.hwnd, "title": room.window.title,
                "composer_hwnd": room.composer, "history_hwnd": room.history,
                "opened_here": room.opened_here}

    def composer_text(self, hwnd):
        return self.api.draft_text(hwnd)

    def candidates(self, title: str) -> list[Room]:
        return [room for window in self.api.top_windows()
                if same_title(window.title, title) and (room := self.api.as_room(window))]

    def resolve(self, title: str, hwnd: int | None = None, *, allow_search=True) -> Room:
        if not title or "\0" in title:
            raise KakaoError("INVALID_ROOM", "Provide the exact nonempty room title.")
        rooms = self.candidates(title)
        if hwnd is not None:
            rooms = [r for r in rooms if r.window.hwnd == hwnd]
            if len(rooms) != 1:
                raise KakaoError("ROOM_HANDLE_MISMATCH", "The supplied handle is not an exact matching open room.")
        if len(rooms) > 1:
            raise KakaoError("AMBIGUOUS_ROOM", "Multiple open rooms have that title; use --room-hwnd.",
                             candidates=[self.describe_room(r) for r in rooms])
        if rooms:
            self.assert_room(rooms[0], title)
            return rooms[0]
        if not allow_search:
            raise KakaoError("ROOM_NOT_OPEN", "Open and verify this conversation first, then use its current --room-hwnd for writing.")

        search = self.api.search_edit()
        previous = self.api.get_text(search)
        before = {w.hwnd for w in self.api.top_windows()}
        try:
            self.api.set_text(search, title)
            time.sleep(0.6)
            # These messages go only to the observed chat-search Edit control.
            for index in range(8):
                self.api.key(search, 0x28)  # Down: select the next result.
                self.api.key(search, 0x0D)  # Enter only in the search control.
                deadline = time.monotonic() + 0.8
                while time.monotonic() < deadline:
                    rooms = self.candidates(title)
                    if len(rooms) > 1:
                        raise KakaoError("AMBIGUOUS_ROOM", "Multiple matching chat windows appeared.")
                    if rooms:
                        r = rooms[0]
                        room = Room(r.window, r.composer, r.history, r.window.hwnd not in before)
                        self.assert_room(room, title)
                        return room
                    time.sleep(0.1)
                # Discard only newly opened, empty-composer partial matches.
                for w in self.api.top_windows():
                    if w.hwnd not in before and not same_title(w.title, title):
                        partial = self.api.as_room(w)
                        if partial and self.api.get_text(partial.composer) == "":
                            self.api.close(w.hwnd)
            raise KakaoError("ROOM_NOT_FOUND", "No exact matching native chat window appeared; no message was written.")
        finally:
            # Restore only our own unchanged query. Preserve a query typed by the user meanwhile.
            if self.api.valid(search) and self.api.get_text(search) == title:
                self.api.set_text(search, previous)

    def assert_room(self, room: Room, title: str) -> None:
        self.api.ensure_ready()
        current = self.api.window(room.window.hwnd)
        actual = self.api.as_room(current)
        if (not same_title(current.title, title) or actual is None
                or actual.composer != room.composer or actual.history != room.history):
            raise KakaoError("ROOM_CHANGED", "The room title or its native controls changed; stopped.")

    def read(self, title: str, hwnd=None, limit=30, max_chars=16000, close_after=False) -> dict:
        room = self.resolve(title, hwnd)
        try:
            self.assert_room(room, title)
            raw, clip = self.api.copy_history(room.history)
            messages = parse_history(raw)
            selected = messages[-limit:]
            text = "\n\n".join(
                f"{m['date'] or ''}\n[{m['sender']}] [{m['time']}] {m['text']}" for m in selected
            ) if selected else normalized_text(raw)
            cut = len(text) > max_chars
            if cut:
                text = text[-max_chars:]
            # Bound both representations, including a single oversized multiline message.
            budget = max_chars
            kept = []
            first_cut = False
            for m in reversed(selected):
                cost = len(m["text"])
                if cost > budget:
                    if budget:
                        kept.append({**m, "text": m["text"][-budget:], "truncated": True})
                        first_cut = True
                    break
                kept.append(m)
                budget -= cost
            return {"status": "read", "room": self.describe_room(room), "text": text,
                    "messages": list(reversed(kept)), "copied_message_count": len(messages),
                    "copied_char_count": len(raw), "text_truncated": cut,
                    "truncated_first_message": first_cut, "coverage": "locally_copyable_history",
                    **clip}
        finally:
            if close_after and room.opened_here:
                try:
                    self.assert_room(room, title)
                    if self.composer_text(room.composer) == "":
                        self.api.close(room.window.hwnd)
                except Exception:
                    pass  # Never close a changed/unverified room during cleanup.

    def write(self, title: str, text: str, *, hwnd=None, mode="draft", execute=False,
              append=False, replace_draft=False, send_key="enter") -> dict:
        if not text or not text.strip() or "\0" in text:
            raise KakaoError("INVALID_TEXT", "Text must be nonempty and contain no NUL characters.")
        if mode == "send" and execute and hwnd is None:
            raise KakaoError("ROOM_HANDLE_REQUIRED", "Use the verified current --room-hwnd from open/read/inspect or the send plan.")
        room = self.resolve(title, hwnd, allow_search=False)
        self.assert_room(room, title)
        existing = self.composer_text(room.composer)
        desired = existing + text if append else text
        if existing and normalized_text(existing) != normalized_text(desired) and not (append or replace_draft):
            raise KakaoError("DRAFT_EXISTS", "A different draft is present; use authorized --append or --replace-draft.")
        plan = {"room": self.describe_room(room), "text": desired, "send_key": send_key}
        if mode == "send" and not execute:
            return {"status": "planned", "execute_required": True, **plan}

        self.assert_room(room, title)
        if self.composer_text(room.composer) != existing:
            raise KakaoError("DRAFT_CHANGED", "The user changed the draft while this command was preparing it.")
        if normalized_text(existing) != normalized_text(desired):
            self.api.set_text(room.composer, desired)
        self.assert_room(room, title)
        if normalized_text(self.api.get_text(room.composer)) != normalized_text(desired):
            raise KakaoError("WRITE_NOT_VERIFIED", "The composer does not contain the requested text; no send shortcut issued.")
        if mode == "draft":
            return {"status": "drafted", **plan}

        # After this point every failure is an unknown send outcome. Never retry.
        try:
            self.api.send_key(room.composer, ctrl=(send_key == "ctrl-enter"))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                self.assert_room(room, title)
                remaining = self.composer_text(room.composer)
                if remaining == "":
                    return {"status": "submitted", "delivery_confirmed": False, **plan}
                if normalized_text(remaining) not in (normalized_text(desired), normalized_text(desired) + "\n"):
                    break
                time.sleep(0.1)
        except Exception as exc:
            if isinstance(exc, KakaoError) and exc.details.get("dispatch_started") is False:
                raise
            raise KakaoError("SEND_OUTCOME_UNKNOWN", "Send shortcut may have been processed. Inspect the room before proceeding; do not retry.",
                             room=self.describe_room(room), cause=getattr(exc, "code", type(exc).__name__)) from exc
        raise KakaoError("SEND_OUTCOME_UNKNOWN", "The composer did not clear as expected. Inspect the room; do not retry or switch shortcuts automatically.",
                         room=self.describe_room(room))


class ClipboardSnapshot:
    """Eagerly duplicate clipboard handles and transfer them back on restoration."""

    def __init__(self, api):
        self.api = api
        self.saved: list[tuple[int, int]] = []
        self.owned_sequence = None
        self.external_change = False
        self.restored = False
        self.owner = 0

    def backup(self):
        """Called while the clipboard is open; this does not change its contents."""
        a = self.api
        fmt = 0
        while True:
            fmt = a.clip.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt == 0x80 or 0x200 <= fmt <= 0x3FF:
                raise KakaoError("CLIPBOARD_UNSUPPORTED", "Cannot preserve an owner-specific clipboard format; no copy performed.", format=fmt)
            handle = a.user.GetClipboardData(fmt)
            duplicate = a.duplicate_clipboard_handle(fmt, handle) if handle else 0
            if not duplicate:
                raise KakaoError("CLIPBOARD_UNSUPPORTED", "Cannot duplicate a clipboard format; no copy performed.", format=fmt)
            self.saved.append((fmt, duplicate))

    def __enter__(self):
        a = self.api
        self.owner = a.gui.CreateWindowEx(0, "STATIC", "kakaoctl-clipboard", 0,
                                          0, 0, 0, 0, -3, 0, 0, None)
        try:
            with a.open_clipboard(self.owner):
                self.backup()
                a.clip.EmptyClipboard()
                self.owned_sequence = a.clip.GetClipboardSequenceNumber()
            return self
        except Exception:
            if self.owned_sequence is not None:
                self.__exit__(*sys.exc_info())
            else:
                self.dispose()
            raise

    def capture(self, timeout=3) -> str:
        a = self.api
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Kakao's EmptyClipboard sends WM_DESTROYCLIPBOARD to our owner window.
            # Service that synchronous message while waiting for its copy to finish.
            if hasattr(a.gui, "PumpWaitingMessages"):
                a.gui.PumpWaitingMessages()
            if a.clip.GetClipboardSequenceNumber() != self.owned_sequence:
                with a.open_clipboard(self.owner):
                    source = a.clip.GetClipboardOwner()
                    source_pid = a.proc.GetWindowThreadProcessId(source)[1] if source else 0
                    if source_pid != a.pid:
                        self.external_change = True
                        raise KakaoError("CLIPBOARD_CHANGED", "Another application changed the clipboard; left its newer content intact.")
                    self.owned_sequence = a.clip.GetClipboardSequenceNumber()
                    if a.clip.IsClipboardFormatAvailable(13):
                        text = a.clip.GetClipboardData(13)
                        # Synthesizing Unicode from another text format can change the sequence.
                        self.owned_sequence = a.clip.GetClipboardSequenceNumber()
                        return text
            time.sleep(0.05)
        raise KakaoError("COPY_TIMEOUT", "KakaoTalk did not supply fresh Unicode history; no stale clipboard text was used.")

    def __exit__(self, exc_type, exc, tb):
        a = self.api
        try:
            with a.open_clipboard(self.owner):
                changed = a.clip.GetClipboardSequenceNumber() != self.owned_sequence
                owner = a.clip.GetClipboardOwner()
                source_pid = a.proc.GetWindowThreadProcessId(owner)[1] if owner else 0
                if self.external_change or (changed and source_pid != a.pid):
                    return False
                a.clip.EmptyClipboard()
                failures = []
                for i, (fmt, handle) in enumerate(self.saved):
                    if a.user.SetClipboardData(fmt, handle):
                        self.saved[i] = (fmt, 0)  # Clipboard now owns it.
                    else:
                        failures.append(fmt)
                self.restored = not failures
                if failures:
                    raise KakaoError("CLIPBOARD_RESTORE_FAILED", "Some preserved clipboard formats could not be restored.", formats=failures)
        finally:
            self.dispose()
        return False

    def dispose(self):
        for fmt, handle in self.saved:
            if handle:
                self.api.free_clipboard_handle(fmt, handle)
        self.saved.clear()
        if self.owner:
            self.api.gui.DestroyWindow(self.owner)
            self.owner = 0


class Win32:
    def __init__(self, pid=None):
        if os.name != "nt":
            raise KakaoError("WINDOWS_REQUIRED", "Run this helper on the Windows PC running KakaoTalk.")
        try:
            import win32clipboard
            import win32gui
            import win32process
            import win32api
        except ImportError as exc:
            raise KakaoError("PYWIN32_REQUIRED", "Use an interpreter with pywin32 installed.") from exc
        self.clip, self.gui, self.proc, self.api = win32clipboard, win32gui, win32process, win32api
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ole = ctypes.WinDLL("ole32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        ptr = ctypes.c_size_t
        signatures = [
            (self.user.SendMessageTimeoutW, [wt.HWND, wt.UINT, ptr, ctypes.c_ssize_t, wt.UINT, wt.UINT, ctypes.POINTER(ptr)], ptr),
            (self.user.GetClipboardData, [wt.UINT], wt.HANDLE),
            (self.user.SetClipboardData, [wt.UINT, wt.HANDLE], wt.HANDLE),
            (self.user.SendInput, [wt.UINT, ctypes.POINTER(NativeInput), ctypes.c_int], wt.UINT),
            (self.user.GetAsyncKeyState, [ctypes.c_int], ctypes.c_short),
            (self.user.AttachThreadInput, [wt.DWORD, wt.DWORD, wt.BOOL], wt.BOOL),
            (self.user.GetKeyboardState, [ctypes.c_void_p], wt.BOOL),
            (self.user.SetKeyboardState, [ctypes.c_void_p], wt.BOOL),
            (self.kernel.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            (self.kernel.QueryFullProcessImageNameW, [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)], wt.BOOL),
            (self.kernel.CloseHandle, [wt.HANDLE], wt.BOOL),
            (self.kernel.GlobalFree, [wt.HANDLE], wt.HANDLE),
            (self.kernel.GlobalLock, [wt.HANDLE], ctypes.c_void_p),
            (self.kernel.GlobalUnlock, [wt.HANDLE], wt.BOOL),
            (self.kernel.GetCurrentThreadId, [], wt.DWORD),
            (self.kernel.CreateMutexW, [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR], wt.HANDLE),
            (self.kernel.WaitForSingleObject, [wt.HANDLE, wt.DWORD], wt.DWORD),
            (self.kernel.ReleaseMutex, [wt.HANDLE], wt.BOOL),
            (self.ole.OleDuplicateData, [wt.HANDLE, wt.WORD, wt.UINT], wt.HANDLE),
            (self.gdi.CopyEnhMetaFileW, [wt.HANDLE, wt.LPCWSTR], wt.HANDLE),
            (self.gdi.DeleteObject, [wt.HANDLE], wt.BOOL),
            (self.gdi.DeleteEnhMetaFile, [wt.HANDLE], wt.BOOL),
            (self.gdi.DeleteMetaFile, [wt.HANDLE], wt.BOOL),
        ]
        for fn, args, result in signatures:
            fn.argtypes, fn.restype = args, result
        mains = []
        for w in self._top_all():
            if (w.title in ("카카오톡", "KakaoTalk") and (pid is None or w.pid == pid)
                    and self._exe(w.pid).lower().endswith("\\kakaotalk.exe")
                    and self._is_main_window(w.hwnd)):
                mains.append(w)
        if len(mains) != 1:
            raise KakaoError("APP_NOT_UNIQUE", "Expected one KakaoTalk main window; start it or select --pid.",
                             candidates=[asdict(w) for w in mains])
        self.main, self.pid = mains[0].hwnd, mains[0].pid
        self.ensure_ready()

    def _is_main_window(self, hwnd):
        found = []
        def callback(child, _):
            if self.gui.GetWindowText(child).startswith("OnlineMainView_"):
                found.append(child)
            return True
        self.gui.EnumChildWindows(hwnd, callback, None)
        return bool(found)

    def _exe(self, pid):
        h = self.kernel.OpenProcess(0x1000, False, pid)
        if not h:
            return ""
        try:
            buf, size = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
            return buf.value if self.kernel.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else ""
        finally:
            self.kernel.CloseHandle(h)

    def _window(self, hwnd):
        return Window(hwnd, self.proc.GetWindowThreadProcessId(hwnd)[1], self.gui.GetClassName(hwnd),
                      self.gui.GetWindowText(hwnd), self.gui.GetParent(hwnd), bool(self.gui.IsWindowVisible(hwnd)))

    def _top_all(self):
        rows = []
        def callback(hwnd, _):
            try:
                rows.append(self._window(hwnd))
            except Exception:
                pass
            return True
        self.gui.EnumWindows(callback, None)
        return rows

    def top_windows(self):
        return [w for w in self._top_all() if w.pid == self.pid]

    def valid(self, hwnd):
        return self.gui.IsWindow(hwnd) and self.proc.GetWindowThreadProcessId(hwnd)[1] == self.pid

    def window(self, hwnd):
        if not self.valid(hwnd) or not self._exe(self.pid).lower().endswith("\\kakaotalk.exe"):
            raise KakaoError("STALE_HANDLE", "The handle no longer belongs to the selected KakaoTalk process.")
        return self._window(hwnd)

    def children(self, parent):
        self.window(parent)
        rows = []
        def callback(hwnd, _):
            try:
                rows.append(self._window(hwnd))
            except Exception:
                pass
            return True
        self.gui.EnumChildWindows(parent, callback, None)
        return [w for w in rows if w.pid == self.pid]

    def ensure_ready(self):
        children = self.children(self.main)
        if any(w.visible and w.title.startswith("LockModeView_") for w in children):
            raise KakaoError("APP_LOCKED", "Unlock KakaoTalk manually before using this helper.")
        if not any(w.title.startswith("OnlineMainView_") for w in children):
            raise KakaoError("APP_NOT_READY", "The logged-in native KakaoTalk view is not present.")

    def as_room(self, window):
        self.window(window.hwnd)
        if window.hwnd == self.main or not window.visible or not window.class_name.startswith("EVA_"):
            return None
        children = self.children(window.hwnd)
        composers = [w.hwnd for w in children if w.class_name.upper().startswith("RICHEDIT") and w.visible]
        histories = [w.hwnd for w in children if w.class_name.startswith("EVA_VH_ListControl") and w.visible]
        if len(composers) == len(histories) == 1:
            return Room(window, composers[0], histories[0])
        return None

    def search_edit(self):
        self.ensure_ready()
        views = [w for w in self.children(self.main) if w.title.startswith("ChatRoomListView_")]
        if len(views) != 1 or not views[0].visible:
            raise KakaoError("CHAT_TAB_HIDDEN", "Show the KakaoTalk chats tab once, or use an already open room.")
        edits = [w for w in self.children(views[0].hwnd) if w.class_name == "Edit" and w.parent == views[0].hwnd]
        if len(edits) != 1:
            raise KakaoError("SEARCH_CONTROL_CHANGED", "The native chat search Edit is not uniquely identifiable.")
        return edits[0].hwnd

    def message(self, hwnd, msg, wparam=0, lparam=0):
        self.window(hwnd)
        result = ctypes.c_size_t()
        if not self.user.SendMessageTimeoutW(hwnd, msg, wparam, lparam, 0x23, 2000, ctypes.byref(result)):
            raise KakaoError("WINDOW_TIMEOUT", "A native window did not respond; stopped without retrying input.")
        return result.value

    def get_text(self, hwnd):
        size = self.message(hwnd, 0x000E)
        if size > 1_000_000:
            raise KakaoError("TEXT_TOO_LARGE", "Unexpectedly large native control text.")
        buf = ctypes.create_unicode_buffer(size + 1)
        self.message(hwnd, 0x000D, size + 1, ctypes.addressof(buf))
        return buf.value

    def draft_text(self, hwnd):
        raw = self.get_text(hwnd)
        # Kakao keeps the empty editor's cue as actual RichEdit window text.
        if self.window(hwnd).class_name.upper().startswith("RICHEDIT") and raw in (
                "메시지 입력", "메세지 입력", "Enter a message", "Type a message"):
            return ""
        return raw

    def set_text(self, hwnd, text):
        if self.window(hwnd).class_name != "Edit" and not self.window(hwnd).class_name.upper().startswith("RICHEDIT"):
            raise KakaoError("NOT_EDIT_CONTROL", "Text may only be written to an identified Edit/RichEdit control.")
        buf = ctypes.create_unicode_buffer(text)
        if self.window(hwnd).class_name.upper().startswith("RICHEDIT"):
            self.focus(hwnd)
            # EM_REPLACESEL produces the editor change notifications needed by
            # current Kakao builds; WM_SETTEXT alone can leave the send path idle.
            self.message(hwnd, 0x00B1, 0, -1)  # EM_SETSEL: select all in the composer.
            self.message(hwnd, 0x00C2, 1, ctypes.addressof(buf))
        else:
            self.message(hwnd, 0x000C, 0, ctypes.addressof(buf))

    @contextmanager
    def ctrl_state(self, hwnd):
        """Keep Ctrl held until the caller observes the native operation's effect."""
        self.window(hwnd)
        old = (ctypes.c_ubyte * 256)()
        attached = False
        snapshotted = False
        try:
            self.gui.PeekMessage(None, 0, 0, 0)
            source_tid = self.kernel.GetCurrentThreadId()
            target_tid = self.proc.GetWindowThreadProcessId(hwnd)[0]
            if not self.user.AttachThreadInput(source_tid, target_tid, True):
                raise KakaoError("KEYBOARD_ATTACH_FAILED", "Could not target the native control's input queue.", dispatch_started=False)
            attached = True
            if not self.user.GetKeyboardState(ctypes.byref(old)):
                raise KakaoError("KEYBOARD_STATE_FAILED", "Could not snapshot keyboard state.", dispatch_started=False)
            snapshotted = True
            modified = (ctypes.c_ubyte * 256).from_buffer_copy(old)
            modified[0x11] |= 0x80
            modified[0xA2] |= 0x80
            self.gui.SetFocus(hwnd)
            if not self.user.SetKeyboardState(ctypes.byref(modified)):
                raise KakaoError("KEYBOARD_STATE_FAILED", "Could not set the temporary Ctrl state.", dispatch_started=False)
            yield
        finally:
            if attached:
                if snapshotted:
                    self.user.SetKeyboardState(ctypes.byref(old))
                self.user.AttachThreadInput(source_tid, target_tid, False)

    def focus(self, hwnd):
        self.window(hwnd)
        self.gui.PeekMessage(None, 0, 0, 0)
        source_tid = self.kernel.GetCurrentThreadId()
        target_tid = self.proc.GetWindowThreadProcessId(hwnd)[0]
        foreground_tid = 0
        if hasattr(self.gui, "GetForegroundWindow"):
            foreground = self.gui.GetForegroundWindow()
            if foreground:
                foreground_tid = self.proc.GetWindowThreadProcessId(foreground)[0]
        foreground_attached = False
        if foreground_tid and foreground_tid not in (source_tid, target_tid):
            foreground_attached = bool(self.user.AttachThreadInput(source_tid, foreground_tid, True))
        if not self.user.AttachThreadInput(source_tid, target_tid, True):
            if foreground_attached:
                self.user.AttachThreadInput(source_tid, foreground_tid, False)
            raise KakaoError("KEYBOARD_ATTACH_FAILED", "Could not focus the native input control.", dispatch_started=False)
        try:
            if hasattr(self.gui, "GetAncestor"):
                root = self.gui.GetAncestor(hwnd, 2)  # GA_ROOT excludes a popup's owner.
            else:
                root = hwnd
                while self.gui.GetParent(root):
                    root = self.gui.GetParent(root)
            try:
                self.gui.SetForegroundWindow(root)
            except Exception:
                pass  # Background input can still work; verify the target focus below.
            self.gui.SetActiveWindow(root)
            self.gui.SetFocus(hwnd)
            if self.gui.GetFocus() != hwnd:
                raise KakaoError("FOCUS_NOT_VERIFIED", "The requested input control did not receive focus.", dispatch_started=False)
            if hasattr(self.gui, "GetForegroundWindow") and self.gui.GetForegroundWindow() != root:
                raise KakaoError("FOREGROUND_NOT_VERIFIED", "The verified chat window did not become active; no send key issued.", dispatch_started=False)
        finally:
            self.user.AttachThreadInput(source_tid, target_tid, False)
            if foreground_attached:
                self.user.AttachThreadInput(source_tid, foreground_tid, False)

    def key(self, hwnd, key, ctrl=False):
        self.window(hwnd)
        if ctrl:
            with self.ctrl_state(hwnd):
                self.key(hwnd, key)
                if key == 0x0D:
                    deadline = time.monotonic() + 3
                    while self.get_text(hwnd) != "":
                        if time.monotonic() >= deadline:
                            raise KakaoError("KEY_ACK_TIMEOUT", "Ctrl+Enter was posted but its effect is unconfirmed.", dispatch_started=True)
                        time.sleep(0.05)
            return
        if key == 0x0D and self.window(hwnd).class_name.upper().startswith("RICHEDIT"):
            self.focus(hwnd)
        scan = self.api.MapVirtualKey(key, 0)
        lp = 1 | (scan << 16)
        if key in (0x25, 0x26, 0x27, 0x28):
            lp |= 0x01000000
        self.gui.PostMessage(hwnd, 0x0100, key, lp)
        time.sleep(0.08)
        self.gui.PostMessage(hwnd, 0x0101, key, lp | 0xC0000000)
        time.sleep(0.18)

    def _native_key_event(self, key, up=False):
        event = NativeInput()
        event.type = 1
        event.payload.keyboard = KeyboardInput(key, 0, 2 if up else 0, 0, 0)
        return self.user.SendInput(1, ctypes.byref(event), ctypes.sizeof(NativeInput)) == 1

    def send_key(self, hwnd, ctrl=False):
        if not self.window(hwnd).class_name.upper().startswith("RICHEDIT"):
            raise KakaoError("NOT_EDIT_CONTROL", "Send key must target the verified composer.", dispatch_started=False)
        self.focus(hwnd)
        if any(self.user.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12)):
            raise KakaoError("MODIFIER_HELD", "A physical modifier key is held; no send key issued.", dispatch_started=False)
        pressed = []
        dispatched = False
        try:
            if ctrl:
                if not self._native_key_event(0x11):
                    raise RuntimeError("Ctrl key injection was rejected")
                pressed.append(0x11)
                time.sleep(0.05)
            if not self._native_key_event(0x0D):
                raise RuntimeError("Enter key injection was rejected")
            dispatched = True
            pressed.append(0x0D)
            # Down/up in one batch can be ignored by Kakao; hold for a real stroke.
            time.sleep(0.15)
            if not self._native_key_event(0x0D, up=True):
                raise RuntimeError("Enter key release was rejected")
            pressed.remove(0x0D)
            if ctrl:
                deadline = time.monotonic() + 3
                while self.draft_text(hwnd) and time.monotonic() < deadline:
                    self.gui.PumpWaitingMessages()
                    time.sleep(0.05)
        except Exception as exc:
            raise KakaoError("NATIVE_KEY_FAILED", str(exc), dispatch_started=dispatched) from exc
        finally:
            for key in reversed(pressed):
                self._native_key_event(key, up=True)

    @contextmanager
    def open_clipboard(self, owner):
        deadline = time.monotonic() + 1.5
        while True:
            try:
                self.clip.OpenClipboard(owner)
                break
            except Exception as exc:
                if time.monotonic() >= deadline:
                    raise KakaoError("CLIPBOARD_BUSY", "The clipboard is busy; no forced clipboard takeover.") from exc
                self.gui.PumpWaitingMessages()
                time.sleep(0.05)
        try:
            yield
        finally:
            self.clip.CloseClipboard()

    def free_clipboard_handle(self, fmt, handle):
        if fmt in (2, 9, 0x82):
            self.gdi.DeleteObject(handle)
        elif fmt in (14, 0x8E):
            self.gdi.DeleteEnhMetaFile(handle)
        elif fmt in (3, 0x83):
            class MetaPicture(ctypes.Structure):
                _fields_ = [("mm", ctypes.c_int), ("x", ctypes.c_int), ("y", ctypes.c_int), ("meta", wt.HANDLE)]
            ptr = self.kernel.GlobalLock(handle)
            if ptr:
                try:
                    self.gdi.DeleteMetaFile(MetaPicture.from_address(ptr).meta)
                finally:
                    self.kernel.GlobalUnlock(handle)
            self.kernel.GlobalFree(handle)
        else:
            self.kernel.GlobalFree(handle)

    def duplicate_clipboard_handle(self, fmt, handle):
        if fmt in (14, 0x8E):
            return self.gdi.CopyEnhMetaFileW(handle, None)
        return self.ole.OleDuplicateData(handle, {0x82: 2, 0x83: 3}.get(fmt, fmt), 0)

    def copy_history(self, hwnd):
        if not self.window(hwnd).class_name.startswith("EVA_VH_ListControl"):
            raise KakaoError("NOT_HISTORY", "Copy may only target an identified history control.")
        with ClipboardSnapshot(self) as snapshot:
            with self.ctrl_state(hwnd):
                self.key(hwnd, ord("A"))
                self.key(hwnd, ord("C"))
                text = snapshot.capture()
        self.key(hwnd, 0x23)  # End: leave the most recent item, not the whole history, selected.
        return text, {"clipboard_restored": snapshot.restored,
                      "clipboard_restore_skipped": not snapshot.restored}

    def close(self, hwnd):
        self.window(hwnd)
        self.gui.PostMessage(hwnd, 0x0010, 0, 0)

    @contextmanager
    def exclusive(self):
        handle = self.kernel.CreateMutexW(None, False, f"Local\\kakaotalk-win32-{self.pid}")
        if not handle:
            raise KakaoError("MUTEX_FAILED", "Could not create the per-process command lock.")
        acquired = False
        try:
            result = self.kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 0x80):
                raise KakaoError("COMMAND_RUNNING", "Another helper command is already operating this KakaoTalk process.")
            acquired = True
            yield
        finally:
            if acquired:
                self.kernel.ReleaseMutex(handle)
            self.kernel.CloseHandle(handle)

    def inspect(self, tree=False):
        return {"status": "inspected", "pid": self.pid, "exe": self._exe(self.pid),
                "main_hwnd": self.main,
                "windows": [asdict(w) for w in self.top_windows() if w.visible and w.title],
                "chat_tab_visible": any(w.title.startswith("ChatRoomListView_") and w.visible for w in self.children(self.main)),
                **({"controls": [asdict(w) for w in self.children(self.main)]} if tree else {})}


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be a positive integer.")
    return number


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--pid", type=positive, help="Select one KakaoTalk PID when several clients exist.")
    sub = root.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="Discover current app/windows without input.")
    inspect.add_argument("--tree", action="store_true")
    sub.add_parser("selected", help="Open the current chat-list selection and report its title/handles, without reading history.")
    leave = sub.add_parser("leave", help="Plan or execute explicitly requested leaving of a verified selected chat.")
    leave.add_argument("--room", required=True, help="Exact title of the already verified room.")
    leave.add_argument("--room-hwnd", type=lambda x: int(x, 0), required=True)
    leave.add_argument("--execute", action="store_true")
    leave.add_argument("--timeout", type=positive, default=3, help="Seconds to wait for each leave-dialog transition.")
    attach = sub.add_parser("attach", help="Plan or send one file/image/video to a verified open room.")
    attach.add_argument("--room", required=True)
    attach.add_argument("--room-hwnd", type=lambda x: int(x, 0), required=True)
    attach.add_argument("--file", type=Path, required=True)
    attach.add_argument("--kind", choices=("auto", "file", "image", "video"), default="auto")
    attach.add_argument("--execute", action="store_true")
    attach.add_argument("--timeout", type=positive, default=30)
    attach.add_argument("--receipt", type=Path, help="Optional local JSON result/unknown-outcome receipt.")
    resume = sub.add_parser("attach-resume", help="Reconcile an attachment receipt without reopening its file picker.")
    resume.add_argument("--receipt", type=Path, required=True)
    resume.add_argument("--confirm", action="store_true", help="Confirm only a still-open popup recorded in this authorized attempt.")
    resume.add_argument("--timeout", type=positive, default=30)
    for command in ("open", "read", "draft", "send"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--room", required=True, help="Exact chat window title.")
        cmd.add_argument("--room-hwnd", type=lambda x: int(x, 0), help="Verified open room HWND (decimal or 0x...).")
        if command == "read":
            cmd.add_argument("--limit", type=positive, default=30)
            cmd.add_argument("--max-chars", type=positive, default=16000)
            cmd.add_argument("--close-after", action="store_true", help="Close only a room opened by this read; avoid for read-then-write workflows.")
        if command in ("draft", "send"):
            text = cmd.add_mutually_exclusive_group(required=True)
            text.add_argument("--text")
            text.add_argument("--text-file", type=Path, help="UTF-8/BOM UTF-8 text file.")
            mode = cmd.add_mutually_exclusive_group()
            mode.add_argument("--replace-draft", action="store_true")
            if command == "draft":
                mode.add_argument("--append", action="store_true")
            else:
                cmd.add_argument("--execute", action="store_true", help="Actually issue the send shortcut once.")
                cmd.add_argument("--send-key", choices=("enter", "ctrl-enter"), default="enter")
    return root


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
        sys.stderr.reconfigure(encoding="utf-8", errors="strict")
    args = parser().parse_args()
    try:
        text = args.text_file.read_text(encoding="utf-8-sig") if getattr(args, "text_file", None) else getattr(args, "text", None)
        api = Win32(args.pid)
        app = Controller(api)
        with api.exclusive():
            if args.command == "inspect":
                result = api.inspect(args.tree)
            elif args.command == "selected":
                from kakao_rooms import RoomActions
                result = RoomActions(app).selected()
            elif args.command == "leave":
                from kakao_rooms import RoomActions
                result = RoomActions(app).leave(args.room, args.room_hwnd, execute=args.execute, timeout=args.timeout)
            elif args.command == "open":
                result = {"status": "opened", "room": app.describe_room(app.resolve(args.room, args.room_hwnd))}
            elif args.command == "read":
                result = app.read(args.room, args.room_hwnd, args.limit, args.max_chars, args.close_after)
            elif args.command == "attach":
                from kakao_attachments import AttachmentSender
                result = AttachmentSender(app).send(args.room, args.room_hwnd, args.file,
                                                    kind=args.kind, execute=args.execute, timeout=args.timeout,
                                                    receipt=args.receipt)
            elif args.command == "attach-resume":
                from kakao_attachments import AttachmentSender
                result = AttachmentSender(app).resume(args.receipt, confirm=args.confirm, timeout=args.timeout)
            else:
                result = app.write(args.room, text, hwnd=args.room_hwnd, mode=args.command,
                                   execute=getattr(args, "execute", False), append=getattr(args, "append", False),
                                   replace_draft=args.replace_draft, send_key=getattr(args, "send_key", "enter"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") in ("attachment_outcome_unknown", "leave_outcome_unknown") else 0
    except KakaoError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": exc.message, **exc.details}, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        print(json.dumps({"status": "error", "code": "UNEXPECTED_ERROR", "message": str(exc),
                          "exception": type(exc).__name__}, ensure_ascii=False, indent=2))
        return 3


if __name__ == "__main__":
    sys.modules.setdefault("kakaoctl", sys.modules[__name__])
    raise SystemExit(main())
