# KakaoTalk Win32

Windows용 카카오톡 PC 클라이언트를 제어하는 Codex 스킬과 Python CLI입니다.
창 핸들을 확인하고 Win32 메시지와 네이티브 입력을 사용해 참여 중인 오픈채팅의 대화 읽기, 메시지 작성·전송, 파일·이미지·영상 첨부, 요청한 채팅방 나가기를 수행합니다.

## 기능

| 명령 | 동작 |
| --- | --- |
| `inspect` | 실행 중인 카카오톡과 창 핸들 확인 |
| `selected` | 현재 목록에서 선택된 방을 열고 이름·핸들 확인 (내역 복사 없음) |
| `open` | 이름이 정확히 일치하는 채팅방 열기 |
| `read` | 복사 가능한 대화 내역 읽기 |
| `draft` | 메시지 초안 작성·덧붙이기 |
| `send` | 전송 계획 확인 또는 명시적인 메시지 전송 |
| `attach` | 파일·이미지·영상 첨부 계획 확인 또는 전송 |
| `attach-resume` | 기존 첨부 시도의 결과 확인·미완료 확인 창 처리 |
| `leave` | 확인한 방의 나가기 계획 확인 또는 실행·목록 검색 검증 |

`send`, `attach`, `leave`는 기본적으로 계획만 반환하며, 실제 실행에는 `--execute`가 필요합니다.
작업 대상은 현재 프로세스 ID, 정확한 방 제목, 확인한 창 핸들로 고정합니다.

## 설치

필요 환경: Windows, 로그인된 카카오톡 PC, Python 3.10 이상.
검증에 사용한 Python 버전은 3.11입니다.

PowerShell에서 빈 설치 경로에 저장소를 복제합니다.

```powershell
$skillDir = Join-Path $env:USERPROFILE '.codex\skills\kakaotalk-win32'
git clone https://github.com/gugu9999gu/kakaotalk-win32.git $skillDir
python -m pip install -r "$skillDir\requirements.txt"
python -B -X utf8 "$skillDir\scripts\kakaoctl.py" inspect
```

같은 경로에 스킬이 이미 설치돼 있다면 별도 경로에서 변경 내용을 확인한 뒤 업데이트하세요.
Codex에서 `$kakaotalk-win32`로 호출할 수 있으며, 자세한 작업 지침은 [SKILL.md](SKILL.md)에 있습니다.

## CLI 사용 예

아래 명령은 저장소 루트에서 실행합니다. `123456`은 예시이며, 실제 `inspect` 또는 `open` 결과의 현재 핸들로 바꿔야 합니다.

```powershell
python -B -X utf8 scripts/kakaoctl.py inspect
python -B -X utf8 scripts/kakaoctl.py selected
python -B -X utf8 scripts/kakaoctl.py open --room "대상 방"
python -B -X utf8 scripts/kakaoctl.py read --room "대상 방" --room-hwnd 123456 --limit 20

# 초안 작성
python -B -X utf8 scripts/kakaoctl.py draft --room "대상 방" --room-hwnd 123456 --text "작성할 내용"

# 전송 계획 확인
python -B -X utf8 scripts/kakaoctl.py send --room "대상 방" --room-hwnd 123456 --text "보낼 내용"

# 대상과 문구가 승인된 경우에만 실제 전송
python -B -X utf8 scripts/kakaoctl.py send --room "대상 방" --room-hwnd 123456 --text "보낼 내용" --execute

# 이미지 첨부 계획 확인
python -B -X utf8 scripts/kakaoctl.py attach --room "대상 방" --room-hwnd 123456 --file "C:\example\image.png" --kind image

# 나가기 계획 확인 (실제 나가기에는 명시적인 요청과 --execute 필요)
python -B -X utf8 scripts/kakaoctl.py leave --room "대상 방" --room-hwnd 123456
```

첨부 전송과 결과가 불확실한 시도의 확인 방법은 [첨부 가이드](references/attachments.md)를 참고하세요.
선택한 오픈채팅방 읽기와 나가기 절차는 [오픈채팅 가이드](references/open-chats.md)에 있습니다.

## 동작 범위

- 쓰기 작업은 확인한 카카오톡 창을 전면에 활성화하고 입력창에 포커스를 둡니다.
- 읽을 수 있는 범위는 현재 클라이언트에서 복사 가능한 로컬 대화 내역입니다.
- `selected`는 현재 선택된 방 하나를 확인하며 오픈채팅방 전체 목록이나 새로 가입한 방을 자동으로 판별하지 않습니다.
- 새로운 오픈채팅방에 자동으로 가입하는 기능은 구현하지 않았습니다. 사용자가 입장한 뒤 `selected`와 `read`로 확인할 수 있습니다.
- 나와의 채팅은 실제 프로필 이름과 `나` 표시를 기준으로 식별합니다. 특정 계정 이름이 코드에 고정돼 있지 않습니다.
- 카카오톡 업데이트로 창 구조나 확인 창이 달라지면 제어가 중단될 수 있습니다.
- `verified_in_history`는 대화 기록에서 새 항목을 확인했다는 뜻이며, 서버 수신·다운로드 무결성·영상 재생을 별도로 검증한 상태를 의미하지 않습니다.
- 전송 결과가 불확실하면 대화 기록과 해당 시도의 receipt를 확인해야 합니다. 같은 파일을 자동으로 다시 선택하거나 재전송하지 않습니다.
- 나가기는 목록 선택이 확인한 방 HWND와 일치할 때 진행합니다. 창이 사라지고 로컬 검색에서도 나오지 않아야 `left`를 반환하며, 별도 서버 회원 상태 조회를 뜻하지는 않습니다. `leave_outcome_unknown`이면 자동으로 재시도하지 않습니다.

## 테스트

```powershell
python -B -X utf8 -m unittest discover -s scripts -p "test_*.py"
```

87개 테스트가 가짜 앱·창·클립보드·키 입력 큐와 임시 파일을 사용합니다.
테스트 실행으로 실제 카카오톡에 메시지·파일을 보내거나 채팅방에서 나가지 않습니다.

## 구성

```text
SKILL.md
agents/openai.yaml
references/attachments.md
references/open-chats.md
scripts/kakaoctl.py
scripts/kakao_attachments.py
scripts/kakao_rooms.py
scripts/test_kakaoctl.py
scripts/test_regressions.py
scripts/test_attachments.py
scripts/test_rooms.py
```

개인 채팅 내역, 전송 receipt, 테스트 미디어, 계정별 방 이름은 포함하지 않습니다.
