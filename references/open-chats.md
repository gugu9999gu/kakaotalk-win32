# Open-chat selection and leaving

## Discover the selected room

The native chat list reports a control name, not a usable list of room titles, through `WM_GETTEXT`, MSAA, and UI Automation on the exercised client. An unknown title need not block discovery when the intended room is selected in the main chats tab:

```powershell
python -B -X utf8 "<skill-dir>/scripts/kakaoctl.py" selected
```

The helper identifies the uniquely visible `ChatRoomListCtrl_` or `SearchListCtrl_` under `ChatRoomListView_`, focuses that list, posts Enter to it, and verifies that the foreground window is a native chat belonging to the selected KakaoTalk process. The result contains the exact title, PID, room HWND, composer HWND, and history HWND. It reads no history and sends no message. Opening a room can mark it read.

The selection is the current row, not necessarily the most recently joined room or an open chat. It is not a way to enumerate arbitrary rooms. If the returned title does not fit the user's request, obtain or select the intended destination before reading.

Use the returned exact title and HWND with `read`, `draft`, `send`, or `attach`. Joined open chats have been read through the normal native history-copy path. Copied entries can include voice-room notices, photo/video markers, member-entry notices, and service notices. Media markers do not reveal image contents or voice-room audio. A system entry may be appended to the last parsed message; describe it as an entry notice rather than attributing it to that message's sender.

## Joining limitations

A public invitation's `kakaoopen://join?...` link can launch the installed client, but the observed preview consists of custom EVA windows with no actionable button labels in Win32, MSAA, or UI Automation. Opening that link did not establish membership; neither the new window nor a key press alone proves a successful join. This skill has no automatic join command. When a join cannot be verified, report the boundary and continue after the user completes entry. Never use screenshots/OCR or guess a button coordinate as a fallback for this skill.

## Leave a verified selected chat

```powershell
# Metadata discovery when the intended row is already selected.
python -B -X utf8 "<skill-dir>/scripts/kakaoctl.py" selected

# Substitute the freshly returned title and HWND; 123456 is only an example.
python -B -X utf8 "<skill-dir>/scripts/kakaoctl.py" leave --room "대상 방" --room-hwnd 123456

# Only for a user-requested leave of this destination.
python -B -X utf8 "<skill-dir>/scripts/kakaoctl.py" leave --room "대상 방" --room-hwnd 123456 --execute
```

The default only validates the already open room and reports the effect. Execution does not search for another destination or change the selected row to make it match. It follows the native sequence exercised during a requested open-chat leave:

1. Reopen the current list selection with Enter and require the same room HWND, composer, and history as the bound destination. A different selection, including a different same-title window, stops before Delete.
2. Verify the room and foreground/list identity again, then issue one Delete to the list, never the composer. The per-process helper lock prevents another cooperating command from running concurrently. Keep hands off the client during these foreground transitions; the custom list provides no atomic item-identity API to protect against concurrent manual selection changes.
3. Bind the uniquely new, foreground, visible, enabled, childless `EVA_Window_Dblclk` owned by the main window. This is the observed confirmation shape, not a general dialog matcher; its text is not available through native control labels. Preexisting handles, extra dialogs, a caption, child controls, an inactive popup, or a different owner stop automatic confirmation. A client update may require adapting this version-specific shape.
4. Focus and revalidate that bound popup, require released modifiers, and issue one Enter down/up with a 150 ms hold. Never send Enter to an unrelated chat composer.
5. Wait for both that popup and the original room HWND to disappear. Then use the existing exact-title local search; only `ROOM_NOT_FOUND` counts as local absence. An unavailable chat tab, stale handle, or another search failure does not prove absence. Search can briefly open partial-title results, as described in the main skill.

`status: left` reports `verification: window_closed_and_local_search_absent` and `server_membership_verified: false`. A closed window alone is insufficient. A remaining/reopened same-title room is inconclusive, including when different rooms share a title. Normal window closing (`close-after`, Esc, or `WM_CLOSE`) is not leaving.

Once Delete is dispatched, failures return `leave_outcome_unknown` and exit code 2, with the last phase and, when available, the bound confirmation HWND. Do not automatically retry the leave or confirm a preexisting popup. Inspect the current client and reconcile its state first. This workflow does not promise silent leaving or reuse an invitation link after a leave.

## Verification

Run the offline tests from the skill root:

```powershell
python -B -X utf8 -m unittest discover -s scripts -p "test_*.py"
```

The leave tests use fake windows, key events, time, and membership/search state. They exercise selection mismatch, confirmation binding, single dispatch, key release, and inconclusive verification without entering or leaving a real room. The native sequence was exercised during an explicitly requested real leave; skill maintenance is not authorization to repeat that action. Real room names, chat history, and window handles are not shipped as examples or fixtures.
