"""Kakao native file-picker attachment workflow. Actual sends require --execute."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from kakaoctl import KakaoError, parse_history

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".gif", ".bmp", ".png", ".webp", ".tif", ".tiff", ".heic"}
VIDEO_SUFFIXES = {".mp4", ".m4v", ".avi", ".asf", ".wmv", ".mkv", ".ts", ".mpg", ".mpeg", ".mov", ".flv", ".ogv"}


def file_info(path, kind="auto"):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size == 0:
        raise KakaoError("INVALID_ATTACHMENT", "Attachment must be a nonempty regular file.")
    if '"' in str(path) or "\0" in str(path):
        raise KakaoError("INVALID_ATTACHMENT", "Unsupported quote/NUL in attachment path.")
    inferred = "image" if path.suffix.lower() in IMAGE_SUFFIXES else "video" if path.suffix.lower() in VIDEO_SUFFIXES else "file"
    kind = inferred if kind == "auto" else kind
    if kind not in ("file", "image", "video"):
        raise KakaoError("INVALID_ATTACHMENT_KIND", "Choose file, image, video, or auto.")
    if kind in ("image", "video") and inferred != kind:
        raise KakaoError("ATTACHMENT_KIND_MISMATCH", "The extension does not match the requested media kind.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "name": path.name, "bytes": path.stat().st_size,
            "sha256": digest.hexdigest(), "kind": kind}


def fingerprints(messages):
    return [hashlib.sha256(json.dumps(m, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            for m in messages]


def new_messages(before_hashes, after):
    """Match the longest available baseline suffix without storing private log text."""
    after_hashes = fingerprints(after)
    for length in range(min(len(before_hashes), len(after_hashes)), 0, -1):
        suffix = before_hashes[-length:]
        matches = [start for start in range(len(after_hashes) - length + 1)
                   if after_hashes[start:start + length] == suffix]
        if len(matches) > 1:
            return []  # Repeated anchors cannot establish which occurrence was the baseline.
        if matches:
            return after[matches[0] + length:]
    return after if not before_hashes else []


def attachment_match(message, metadata):
    text = message["text"].strip()
    first_line = text.split("\n", 1)[0]
    for prefix in ("파일:", "File:"):
        if first_line.startswith(prefix) and first_line[len(prefix):].strip() == metadata["name"]:
            return "filename"
    if metadata["kind"] == "image" and text in ("사진", "사진 1장", "Photo", "Image", "[사진]"):
        return "photo_marker"
    if metadata["kind"] == "video" and text in ("동영상", "동영상 1개", "Video", "[동영상]"):
        return "video_marker"
    return None


class AttachmentSender:
    def __init__(self, app):
        self.app, self.api = app, app.api
        self.receipt_versions = {}

    def snapshot(self, room):
        self.app.assert_room(room, room.window.title)
        raw, clipboard = self.api.copy_history(room.history)
        return parse_history(raw), clipboard

    def checkpoint(self, result, receipt):
        if receipt:
            receipt = Path(receipt).resolve()
            source = Path(result["attachment"]["path"]).resolve()
            if receipt == source or (receipt.exists() and source.exists() and os.path.samefile(receipt, source)):
                raise KakaoError("RECEIPT_SOURCE_ALIAS", "Receipt must not refer to the attachment file or one of its hard links.")
            receipt.parent.mkdir(parents=True, exist_ok=True)
            key = str(receipt)
            previous = self.receipt_versions.get(key)
            if previous is None:
                if receipt.exists():
                    raise KakaoError("RECEIPT_EXISTS", "Use a new receipt path or attach-resume; existing files are not overwritten.")
                mode = "x"
            else:
                if not receipt.exists() or hashlib.sha256(receipt.read_bytes()).hexdigest() != previous:
                    raise KakaoError("RECEIPT_CHANGED", "The receipt changed outside this attempt; not overwriting it.")
                mode = "w"
            data = json.dumps(result, ensure_ascii=False, indent=2)
            with receipt.open(mode, encoding="utf-8") as stream:
                stream.write(data)
            self.receipt_versions[key] = hashlib.sha256(receipt.read_bytes()).hexdigest()

    def uncertain_result(self, result, receipt, exc):
        result.update(status="attachment_outcome_unknown", error=str(exc),
                      error_code=getattr(exc, "code", type(exc).__name__),
                      message="Sending may already have happened. Inspect the room; do not reselect/resend automatically.")
        if isinstance(exc, KakaoError):
            result.update(exc.details)
        try:
            self.checkpoint(result, receipt)
        except Exception as receipt_exc:
            result["receipt_write_error"] = str(receipt_exc)
        return self.public_result(result)

    def chord(self, hwnd, key, ctrl=False):
        self.api.focus(hwnd)
        if any(self.api.user.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12)):
            raise KakaoError("MODIFIER_HELD", "Release physical modifier keys before attachment input.")
        pressed = []
        try:
            for k in ([0x11, key] if ctrl else [key]):
                if not self.api._native_key_event(k):
                    raise KakaoError("NATIVE_KEY_FAILED", "Attachment shortcut input was rejected.")
                pressed.append(k)
                time.sleep(0.15)
        finally:
            for k in reversed(pressed):
                self.api._native_key_event(k, up=True)
                time.sleep(0.05)

    def owned_by_room(self, hwnd, room):
        seen = set()
        while hwnd and hwnd not in seen:
            if hwnd == room.window.hwnd:
                return True
            seen.add(hwnd)
            hwnd = self.api.gui.GetWindow(hwnd, 4)  # GW_OWNER
        return False

    def dialogs(self, room, before=()):
        return [w for w in self.api.top_windows() if w.visible and w.hwnd not in before
                and w.hwnd != room.window.hwnd and self.owned_by_room(w.hwnd, room)]

    def controls(self, window):
        return [{"hwnd": w.hwnd, "class": w.class_name, "id": self.api.gui.GetDlgCtrlID(w.hwnd),
                 "text": w.title, "parent": w.parent, "visible": w.visible}
                for w in self.api.children(window.hwnd)]

    def filters(self, dialog):
        result = []
        for w in self.api.children(dialog.hwnd):
            if w.class_name != "ComboBox":
                continue
            count = self.api.message(w.hwnd, 0x0146)
            if count > 30:
                continue
            for index in range(count):
                length = self.api.message(w.hwnd, 0x0149, index)
                if length > 2048:
                    continue
                buf = ctypes.create_unicode_buffer(length + 1)
                self.api.message(w.hwnd, 0x0148, index, ctypes.addressof(buf))
                if "*" in buf.value:
                    result.append((w.hwnd, index, buf.value))
        return result

    def open_picker(self, room):
        before = {w.hwnd for w in self.api.top_windows()}
        if self.dialogs(room):
            raise KakaoError("EXISTING_ATTACHMENT_DIALOG", "An owned dialog is already open; not reusing or closing it.")
        self.chord(room.composer, ord("T"), ctrl=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            dialogs = [w for w in self.dialogs(room, before) if w.class_name == "#32770"]
            if len(dialogs) == 1:
                return dialogs[0], before
            if len(dialogs) > 1:
                break
            self.api.gui.PumpWaitingMessages()
            time.sleep(0.1)
        raise KakaoError("ATTACHMENT_PICKER_NOT_UNIQUE", "No unique new room-owned file picker appeared.")

    def populate_picker(self, dialog, metadata):
        controls = self.controls(dialog)
        fields = [c for c in controls if c["class"] == "Edit" and c["id"] == 1148 and c["visible"]]
        buttons = [c for c in controls if c["class"] == "Button" and c["id"] == 1 and c["visible"]]
        if len(fields) != 1 or len(buttons) != 1:
            raise KakaoError("ATTACHMENT_PICKER_CHANGED", "File-name Edit/Open button could not be identified uniquely.")
        filters = self.filters(dialog)
        needles = {"file": ("모든 파일", "All files"), "image": ("이미지 파일", "Image"),
                   "video": ("비디오 파일", "Video")}[metadata["kind"]]
        choices = [entry for entry in filters if any(word.lower() in entry[2].lower() for word in needles)]
        if len(choices) != 1:
            raise KakaoError("ATTACHMENT_FILTER_CHANGED", "No unique filter for this attachment kind.")
        combo, index, label = choices[0]
        self.api.message(combo, 0x014E, index)  # CB_SETCURSEL
        self.api.message(dialog.hwnd, 0x0111, (1 << 16) | self.api.gui.GetDlgCtrlID(combo), combo)
        self.api.focus(fields[0]["hwnd"])
        self.api.set_text(fields[0]["hwnd"], metadata["path"])
        if self.api.get_text(fields[0]["hwnd"]) != metadata["path"]:
            raise KakaoError("ATTACHMENT_PATH_NOT_VERIFIED", "File picker text differs from the intended path.")
        return buttons[0]["hwnd"], label

    def confirmation(self, room, before, metadata):
        """Confirm only an identified new send dialog; unknown dialogs stop the run."""
        for dialog in self.dialogs(room, before):
            controls = self.controls(dialog)
            buttons = [c for c in controls if c["class"] == "Button" and c["visible"]
                       and c["text"].replace("&", "").strip() in ("전송", "보내기", "Send")]
            purpose = any(word in dialog.title for word in ("전송", "보내기", "Send"))
            file_named = any(metadata["name"] in c["text"] for c in controls)
            if len(buttons) == 1 and (purpose or file_named):
                self.api.focus(buttons[0]["hwnd"])
                self.api.gui.PostMessage(buttons[0]["hwnd"], 0x00F5, 0, 0)  # BM_CLICK
                return {"dialog_title": dialog.title, "method": "native_send_button"}
            if dialog.title in ("사진 전송", "사진 보내기", "동영상 전송", "동영상 보내기", "파일 전송", "파일 보내기"):
                self.chord(dialog.hwnd, 0x0D)
                return {"dialog_title": dialog.title, "method": "native_enter"}
            panes = {c["id"]: c for c in controls if c["class"] in ("EVA_ChildWindow", "EVA_ChildWindow_Dblclk")}
            if (dialog.class_name == "EVA_Window_Dblclk" and dialog.title == ""
                    and 1001 in panes and 1002 in panes and panes[1001]["visible"]
                    and panes[1002]["class"] == "EVA_ChildWindow_Dblclk"):
                # Current native file-send confirmation: no caption/buttons in UIA.
                # Bound to the exact file-picker submission and room-owned popup.
                self.chord(panes[1001]["hwnd"], 0x0D)
                return {"dialog_hwnd": dialog.hwnd, "method": "native_preview_enter",
                        "preview_signature": "EVA/1001+1002"}
            if dialog.class_name == "#32770":
                raise KakaoError("ATTACHMENT_DIALOG_UNRECOGNIZED", "A new dialog needs inspection; no guessed confirmation.",
                                 dialog={"hwnd": dialog.hwnd, "title": dialog.title}, controls=controls)
        return None

    def public_result(self, result):
        output = dict(result)
        baseline = output.pop("baseline_hashes", None)
        if baseline is not None:
            output["baseline_message_count"] = len(baseline)
        output.pop("preexisting_window_handles", None)
        return output

    def verify(self, room, result, before, timeout, receipt):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = self.dialogs(room, before)
            if pending:
                result["pending_windows"] = [{"hwnd": d.hwnd, "title": d.title, "class": d.class_name,
                                              "controls": self.controls(d)} for d in pending]
                break
            after, clip = self.snapshot(room)
            for message in new_messages(result["baseline_hashes"], after):
                match = attachment_match(message, result["attachment"])
                if match:
                    result.pop("pending_windows", None)
                    result.update(status="verified_in_history", verification=match,
                                  message=message, copied_message_count=len(after), **clip)
                    try:
                        self.api.focus(room.composer)
                        result["focus_restored"] = True
                    except Exception as exc:
                        result["focus_restored"] = False
                        result["focus_restore_error"] = str(exc)
                    try:
                        self.checkpoint(result, receipt)
                    except Exception as exc:
                        result["receipt_write_error"] = str(exc)
                    return self.public_result(result)
            time.sleep(0.4)
        result["status"] = "attachment_outcome_unknown"
        result["message"] = "Inspect the room and pending windows before any retry; the file may already have been sent."
        self.checkpoint(result, receipt)
        return self.public_result(result)

    def resume(self, receipt, *, confirm=False, timeout=30):
        receipt_path = Path(receipt).resolve()
        receipt_bytes = receipt_path.read_bytes()
        result = json.loads(receipt_bytes.decode("utf-8"))
        room_info = result["room"]
        if room_info["pid"] != self.api.pid:
            raise KakaoError("ATTACHMENT_PROCESS_CHANGED", "Receipt belongs to another client instance.")
        room = self.app.resolve(room_info["title"], room_info["hwnd"], allow_search=False)
        if (room.composer != room_info["composer_hwnd"] or room.history != room_info["history_hwnd"]):
            raise KakaoError("ATTACHMENT_CONTROLS_CHANGED", "Receipt composer/history handles no longer match this room; no confirmation issued.")
        source = Path(result["attachment"]["path"]).resolve()
        if receipt_path == source or (source.exists() and os.path.samefile(receipt_path, source)):
            raise KakaoError("RECEIPT_SOURCE_ALIAS", "Receipt is the attachment file; refusing to act.")
        self.receipt_versions[str(receipt_path)] = hashlib.sha256(receipt_bytes).hexdigest()
        if result.get("status") == "verified_in_history":
            return self.public_result(result)  # Never confirm/reselect an already verified attempt.
        pending_ids = {d["hwnd"] for d in result.get("pending_windows", [])}
        current_ids = {w.hwnd for w in self.api.top_windows()}
        before = set(result.get("preexisting_window_handles", current_ids - pending_ids))
        if confirm and pending_ids and not result.get("confirmation"):
            if file_info(result["attachment"]["path"], result["attachment"]["kind"]) != result["attachment"]:
                raise KakaoError("ATTACHMENT_CHANGED", "Selected file no longer matches its original receipt.")
            # Resume may confirm the recorded popup, never re-open/reselect a file.
            before |= current_ids - pending_ids
            self.app.assert_room(room, room_info["title"])
            try:
                confirmation = self.confirmation(room, before, result["attachment"])
                if confirmation:
                    result["confirmation"] = confirmation
                    self.checkpoint(result, receipt)
                    time.sleep(0.4)
            except Exception as exc:
                return self.uncertain_result(result, receipt, exc)
        try:
            return self.verify(room, result, before, timeout, receipt)
        except Exception as exc:
            return self.uncertain_result(result, receipt, exc)

    def send(self, title, hwnd, path, *, kind="auto", execute=False, timeout=30, receipt=None):
        metadata = file_info(path, kind)
        room = self.app.resolve(title, hwnd, allow_search=False)
        self.app.assert_room(room, title)
        result = {"status": "planned", "attachment": metadata, "room": self.app.describe_room(room),
                  "execute_required": not execute, "server_delivery_confirmed": False,
                  "attempt_id": uuid.uuid4().hex}
        if not execute:
            return result
        if self.api.draft_text(room.composer):
            raise KakaoError("DRAFT_EXISTS", "A text draft is present; attachment sending will not change it.")
        baseline, _ = self.snapshot(room)
        result["baseline_hashes"] = fingerprints(baseline)
        result["status"] = "preparing_picker"
        self.checkpoint(result, receipt)
        dialog = None
        selection_started = False
        try:
            dialog, before = self.open_picker(room)
            result["preexisting_window_handles"] = sorted(before)
            button, filter_name = self.populate_picker(dialog, metadata)
            if file_info(metadata["path"], metadata["kind"]) != metadata:
                raise KakaoError("ATTACHMENT_CHANGED", "Attachment changed while the picker was open.")
            self.app.assert_room(room, title)
            result.update(status="selection_submitting", filter=filter_name)
            self.checkpoint(result, receipt)
            selection_started = True  # Open can start sending immediately.
            self.api.gui.PostMessage(button, 0x00F5, 0, 0)
            result["status"] = "selection_submitted"
            self.checkpoint(result, receipt)
            time.sleep(0.4)
            self.app.assert_room(room, title)
            confirmation = self.confirmation(room, before, metadata)
            if confirmation:
                result["confirmation"] = confirmation
                self.checkpoint(result, receipt)
                time.sleep(0.3)
            return self.verify(room, result, before, timeout, receipt)
        except Exception as exc:
            if not selection_started:
                if dialog and self.api.valid(dialog.hwnd) and self.owned_by_room(dialog.hwnd, room):
                    self.api.message(dialog.hwnd, 0x0111, 2, 0)
                raise
            return self.uncertain_result(result, receipt, exc)
