"""Attachment workflow tests; no live Windows input or real sending."""
from pathlib import Path
import json
import os
import contextlib
import io
from dataclasses import replace
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from kakaoctl import Controller, KakaoError, Window
from kakao_attachments import AttachmentSender, attachment_match, file_info, fingerprints, new_messages
from test_kakaoctl import FakeApp


def msg(text, minute="01"):
    return {"date": "2026년 11월 17일", "sender": "테스트", "time": "오후 1:" + minute, "text": text}


class SimulatedSender(AttachmentSender):
    def __init__(self, app, filename):
        super().__init__(app)
        self.filename = filename
        self.clicks = []
        self.after_pending = False
        self.copy_count = 0
        self.api.gui = SimpleNamespace(PostMessage=lambda *a: self.clicks.append(a))
        self.api.focus = lambda hwnd: None

    def snapshot(self, room):
        self.copy_count += 1
        messages = [msg("기존 대화")]
        if self.copy_count > 1:
            messages.append(msg("파일: " + self.filename, "02"))
        return messages, {"clipboard_restored": True}

    def open_picker(self, room):
        return Window(300, 7, "#32770", "열기"), {100}

    def populate_picker(self, dialog, metadata):
        return 301, "모든 파일 (*.*)"

    def confirmation(self, room, before, metadata):
        return None

    def dialogs(self, room, before=()):
        return [Window(400, 7, "EVA_Window", "Unknown preview")] if self.after_pending else []

    def controls(self, window):
        return []


class AttachmentTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "unique-test.txt"
        self.path.write_text("non-sensitive test data", encoding="utf-8")
        self.app = Controller(FakeApp())

    def test_metadata_contains_real_path_size_and_hash(self):
        meta = file_info(self.path)
        self.assertEqual(meta["name"], "unique-test.txt")
        self.assertEqual(meta["bytes"], self.path.stat().st_size)
        self.assertEqual(len(meta["sha256"]), 64)
        self.assertEqual(meta["kind"], "file")

    def test_media_kind_validated_before_any_app_action(self):
        with self.assertRaises(KakaoError) as context:
            AttachmentSender(self.app).send("テスト", 100, self.path, kind="video", execute=True)
        self.assertEqual(context.exception.code, "ATTACHMENT_KIND_MISMATCH")

    def test_empty_file_rejected(self):
        self.path.write_bytes(b"")
        with self.assertRaises(KakaoError):
            file_info(self.path)

    def test_missing_file_rejected(self):
        with self.assertRaises(FileNotFoundError):
            file_info(self.path.with_name("missing.png"))

    def test_auto_image_and_video(self):
        for suffix, kind in ((".png", "image"), (".mp4", "video")):
            path = self.path.with_suffix(suffix)
            path.write_bytes(b"test fixture")
            self.assertEqual(file_info(path)["kind"], kind)

    def test_default_plan_does_not_open_dialog_or_send(self):
        sender = SimulatedSender(self.app, self.path.name)
        result = sender.send("테스트 방", 100, self.path)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(sender.clicks, [])
        self.assertEqual(sender.copy_count, 0)

    def test_existing_draft_prevents_attachment(self):
        self.app.api.draft = "사용자 초안"
        sender = SimulatedSender(self.app, self.path.name)
        with self.assertRaises(KakaoError) as context:
            sender.send("테스트 방", 100, self.path, execute=True)
        self.assertEqual(context.exception.code, "DRAFT_EXISTS")
        self.assertEqual(sender.clicks, [])

    def test_exact_room_handle_is_enforced(self):
        sender = SimulatedSender(self.app, self.path.name)
        with self.assertRaises(KakaoError) as context:
            sender.send("테스트 방", 999, self.path, execute=True)
        self.assertEqual(context.exception.code, "ROOM_HANDLE_MISMATCH")

    def test_history_verification_and_single_open_click(self):
        sender = SimulatedSender(self.app, self.path.name)
        receipt = Path(self.temp.name) / "receipt.json"
        with patch("kakao_attachments.time.sleep"):
            result = sender.send("테스트 방", 100, self.path, execute=True, receipt=receipt)
        self.assertEqual(result["status"], "verified_in_history")
        self.assertEqual(result["verification"], "filename")
        self.assertEqual(sender.clicks, [(301, 0xF5, 0, 0)])
        self.assertTrue(receipt.is_file())
        self.assertFalse(result["server_delivery_confirmed"])

    def test_unknown_preview_stops_without_duplicate_selection(self):
        sender = SimulatedSender(self.app, self.path.name)
        sender.after_pending = True
        with patch("kakao_attachments.time.sleep"):
            result = sender.send("테스트 방", 100, self.path, execute=True)
        self.assertEqual(result["status"], "attachment_outcome_unknown")
        self.assertEqual(len(sender.clicks), 1)

    def test_verified_resume_never_selects_or_confirms_again(self):
        first = SimulatedSender(self.app, self.path.name)
        receipt = Path(self.temp.name) / "verified-receipt.json"
        with patch("kakao_attachments.time.sleep"):
            first.send("테스트 방", 100, self.path, execute=True, receipt=receipt)
        resumed = SimulatedSender(self.app, self.path.name)
        result = resumed.resume(receipt, confirm=True)
        self.assertEqual(result["status"], "verified_in_history")
        self.assertEqual(resumed.clicks, [])
        self.assertEqual(resumed.copy_count, 0)

    def test_unknown_resume_does_not_reopen_file_picker(self):
        sender = SimulatedSender(self.app, self.path.name)
        sender.after_pending = True
        receipt = Path(self.temp.name) / "unknown-receipt.json"
        with patch("kakao_attachments.time.sleep"):
            sender.send("테스트 방", 100, self.path, execute=True, receipt=receipt)
            result = sender.resume(receipt, confirm=True)
        self.assertEqual(result["status"], "attachment_outcome_unknown")
        self.assertEqual(len(sender.clicks), 1)

    def test_changed_file_blocks_pending_confirmation(self):
        sender = SimulatedSender(self.app, self.path.name)
        sender.after_pending = True
        receipt = Path(self.temp.name) / "changed-receipt.json"
        with patch("kakao_attachments.time.sleep"):
            sender.send("테스트 방", 100, self.path, execute=True, receipt=receipt)
        self.path.write_text("changed bytes", encoding="utf-8")
        with self.assertRaises(KakaoError) as context:
            sender.resume(receipt, confirm=True)
        self.assertEqual(context.exception.code, "ATTACHMENT_CHANGED")
        self.assertEqual(len(sender.clicks), 1)

    def test_receipt_pid_mismatch_is_rejected(self):
        receipt = Path(self.temp.name) / "wrong-process.json"
        receipt.write_text(json.dumps({"room": {"pid": 999}}), encoding="utf-8")
        with self.assertRaises(KakaoError) as context:
            SimulatedSender(self.app, self.path.name).resume(receipt)
        self.assertEqual(context.exception.code, "ATTACHMENT_PROCESS_CHANGED")

    def test_repeated_media_markers_preserve_new_message_count(self):
        before = [msg("old"), msg("사진"), msg("사진")]
        after = before + [msg("사진")]
        self.assertEqual(new_messages(fingerprints(before), after), [msg("사진")])

    def test_no_overlap_does_not_guess_new_messages(self):
        self.assertEqual(new_messages(fingerprints([msg("old")]), [msg("unrelated")]), [])

    def test_media_marker_matches_only_requested_kind(self):
        self.assertEqual(attachment_match(msg("사진"), {"name": "a.png", "kind": "image"}), "photo_marker")
        self.assertIsNone(attachment_match(msg("사진"), {"name": "a.mp4", "kind": "video"}))
        self.assertEqual(attachment_match(msg("동영상"), {"name": "a.mp4", "kind": "video"}), "video_marker")

    def test_receipt_cannot_overwrite_source(self):
        original = self.path.read_bytes()
        sender = SimulatedSender(self.app, self.path.name)
        with self.assertRaises(KakaoError) as context:
            sender.send("테스트 방", 100, self.path, execute=True, receipt=self.path)
        self.assertEqual(context.exception.code, "RECEIPT_SOURCE_ALIAS")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(sender.clicks, [])

    def test_receipt_cannot_overwrite_source_hardlink(self):
        alias = self.path.with_name("receipt-link.json")
        try:
            os.link(self.path, alias)
        except OSError:
            self.skipTest("Hard links are unavailable on this filesystem")
        original = self.path.read_bytes()
        with self.assertRaises(KakaoError) as context:
            SimulatedSender(self.app, self.path.name).send("테스트 방", 100, self.path, execute=True, receipt=alias)
        self.assertEqual(context.exception.code, "RECEIPT_SOURCE_ALIAS")
        self.assertEqual(self.path.read_bytes(), original)

    def test_existing_unrelated_receipt_is_preserved(self):
        receipt = Path(self.temp.name) / "existing.json"
        receipt.write_text("user-owned data", encoding="utf-8")
        with self.assertRaises(KakaoError) as context:
            SimulatedSender(self.app, self.path.name).send("테스트 방", 100, self.path, execute=True, receipt=receipt)
        self.assertEqual(context.exception.code, "RECEIPT_EXISTS")
        self.assertEqual(receipt.read_text(), "user-owned data")

    def test_filename_verification_is_not_a_substring_search(self):
        metadata = {"name": "specified.txt", "kind": "file"}
        self.assertIsNone(attachment_match(msg("Do not send specified.txt"), metadata))
        self.assertIsNone(attachment_match(msg("파일: NOT-specified.txt"), metadata))
        self.assertEqual(attachment_match(msg("파일: specified.txt"), metadata), "filename")

    def test_ambiguous_old_anchor_does_not_verify_historical_file(self):
        before = [msg("사진")]
        after = [msg("사진"), msg("파일: specified.txt"), msg("사진")]
        self.assertEqual(new_messages(fingerprints(before), after), [])

    def test_resume_checks_saved_composer_and_history_handles(self):
        sender = SimulatedSender(self.app, self.path.name)
        sender.after_pending = True
        receipt = Path(self.temp.name) / "binding.json"
        with patch("kakao_attachments.time.sleep"):
            sender.send("테스트 방", 100, self.path, execute=True, receipt=receipt)
        old = self.app.api.room
        self.app.api.rooms = [replace(old, composer=201, history=202)]
        with self.assertRaises(KakaoError) as context:
            sender.resume(receipt, confirm=True)
        self.assertEqual(context.exception.code, "ATTACHMENT_CONTROLS_CHANGED")
        self.assertEqual(len(sender.clicks), 1)

    def test_journal_failure_after_dispatch_preserves_unknown_status(self):
        sender = SimulatedSender(self.app, self.path.name)
        def fail_late(result, receipt):
            if sender.clicks:
                raise OSError("receipt disk became unavailable")
        sender.checkpoint = fail_late
        with patch("kakao_attachments.time.sleep"):
            result = sender.send("테스트 방", 100, self.path, execute=True, receipt=Path(self.temp.name)/"late.json")
        self.assertEqual(result["status"], "attachment_outcome_unknown")
        self.assertIn("receipt_write_error", result)
        self.assertEqual(len(sender.clicks), 1)

    def test_focus_failure_does_not_discard_verified_history(self):
        sender = SimulatedSender(self.app, self.path.name)
        sender.api.focus = lambda hwnd: (_ for _ in ()).throw(RuntimeError("focus changed"))
        with patch("kakao_attachments.time.sleep"):
            result = sender.send("테스트 방", 100, self.path, execute=True)
        self.assertEqual(result["status"], "verified_in_history")
        self.assertFalse(result["focus_restored"])

    def test_cli_unknown_attachment_outcome_has_nonzero_exit(self):
        import kakaoctl
        fake_api = SimpleNamespace(exclusive=lambda: contextlib.nullcontext())
        with patch("kakaoctl.Win32", return_value=fake_api), \
                patch("kakao_attachments.AttachmentSender") as sender, \
                patch("kakaoctl.sys.argv", ["kakaoctl.py", "attach", "--room", "테스트 방",
                      "--room-hwnd", "100", "--file", str(self.path), "--execute"]), \
                patch("kakaoctl.sys.stdout", new_callable=io.StringIO) as output:
            sender.return_value.send.return_value = {"status": "attachment_outcome_unknown"}
            code = kakaoctl.main()
            result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "attachment_outcome_unknown")
