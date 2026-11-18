# Restore a chat from TalkCloud

Use this workflow for **one existing room** in the Windows KakaoTalk client. It follows **채팅방 메뉴 → 대화 내용 → 톡클라우드 대화 복원**. A TalkCloud backup must exist for the account. Use the Korean KakaoTalk interface, Windows PowerShell 5.1, and Windows OCR with Korean recognition available. Keep the chat and its popups visible.

Open the intended room or use `inspect` to get its actual title, PID, and current HWND. The examples use synthetic handles.

```powershell
# Check the room and scope.
python -B -X utf8 scripts/kakaoctl.py restore --room "대상 방" --room-hwnd 123456

# Open the verified restore dialog for the explicitly requested room.
python -B -X utf8 scripts/kakaoctl.py restore --room "대상 방" --room-hwnd 123456 --execute
```

`awaiting_password` means the correct prompt opened. Ask the user to enter the 6-digit My password with KakaoTalk's on-screen keypad and choose **복원**. Keep the completion notice open for the next command. The script does not take credentials through arguments, environment variables, or chat. The screen reader helper processes only the verified menu rectangles and dialog headings in memory. The heading region ends above the password field.

```powershell
# Replace 234567 with the dialog_hwnd returned by restore.
python -B -X utf8 scripts/kakaoctl.py restore-status --room "대상 방" --room-hwnd 123456 --dialog-hwnd 234567 --wait 30
```

Report `restored` only when the same room owns a visible completion notice that says **대화 복원이 완료되었습니다**. `restore_outcome_unknown` means the notice was unavailable, closed, changed, or unreadable. Inspect the current KakaoTalk state before retrying. There can be zero newly available messages even when KakaoTalk confirms the restore.

After confirmation, close the notice and use the existing `read --scroll-to-start` workflow to check the oldest date, total loaded count, or a requested year. A successful dialog verifies the restore action, while the subsequent read verifies what changed in the PC client's visible data. [KakaoTalk PC room-specific restoration](https://pc.kakao.com/talk/notices/ko/2781?agent=win32)
