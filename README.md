# KakaoTalk Win32

Windows용 카카오톡 PC 클라이언트를 제어하는 에이전트 스킬과 Python CLI입니다. Codex와 Claude Code에 등록할 수 있습니다.
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

## 등록 및 설치

카카오톡 PC에 로그인한 Windows에서 진행하세요. Windows용 Python 3.10 이상이 필요하며, 아래 복제 명령을 사용하려면 Git도 설치되어 있어야 합니다. 카카오톡과 같은 Windows 사용자 세션에서 Python 명령을 실행하세요.

| 사용 환경 | 등록 방법 | 카카오톡 제어 |
| --- | --- | --- |
| Codex 앱·CLI | [Codex](#codex-앱cli) | Windows 로컬 세션 |
| Claude Code CLI | [Claude Code](#claude-code-cli) | Windows 로컬 세션 |
| Claude 데스크톱의 Code | [Claude 데스크톱](#claude-데스크톱) | `Local` 세션 |
| Claude의 Chat·Cowork | [ZIP으로 등록](#chatcowork에-zip으로-등록) | 이 저장소의 Win32 스크립트는 Windows 로컬 세션에서 실행 |

### Codex 앱·CLI

Windows PowerShell에서 아래 명령을 실행하면 이 Windows 계정의 모든 Codex 프로젝트에서 사용할 수 있습니다.

```powershell
$skillDir = Join-Path $env:USERPROFILE '.agents\skills\kakaotalk-win32'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $skillDir) | Out-Null
git clone https://github.com/gugu9999gu/kakaotalk-win32.git "$skillDir"
```

[공통 실행 확인](#공통-실행-확인)을 마친 뒤 Codex 앱의 로컬 프로젝트나 Windows PowerShell에서 시작한 Codex CLI의 새 세션에서 다음을 입력합니다. 스킬이 목록에 보이지 않으면 Codex를 다시 시작하세요. [Codex 공식 스킬 가이드](https://learn.chatgpt.com/docs/build-skills)

> $kakaotalk-win32 실행 중인 카카오톡 PC를 확인해줘.

### Claude Code CLI

Windows PowerShell에서 아래 명령을 실행하면 이 Windows 계정의 Claude Code에 등록됩니다.

```powershell
$skillDir = Join-Path $env:USERPROFILE '.claude\skills\kakaotalk-win32'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $skillDir) | Out-Null
git clone https://github.com/gugu9999gu/kakaotalk-win32.git "$skillDir"
```

[공통 실행 확인](#공통-실행-확인)을 마친 뒤 Windows PowerShell에서 `claude`를 실행하고 다음을 입력합니다. [Claude Code 공식 스킬 가이드](https://code.claude.com/docs/en/skills)

> /kakaotalk-win32 실행 중인 카카오톡 PC를 확인해줘.

### Claude 데스크톱

1. 위 [Claude Code 등록 명령](#claude-code-cli)과 [공통 실행 확인](#공통-실행-확인)을 실행합니다.
2. Claude 데스크톱을 열고 **Code** 탭에서 실행 환경 **Local**을 선택해 새 세션을 엽니다.
3. `/kakaotalk-win32`를 선택하거나 “kakaotalk-win32 스킬로 실행 중인 카카오톡 PC를 확인해줘”라고 입력합니다. [Claude Code 데스크톱 공식 가이드](https://code.claude.com/docs/en/desktop)

#### Chat·Cowork에 ZIP으로 등록

Claude 계정의 스킬 목록에도 추가하려면 [소스 ZIP을 다운로드](https://github.com/gugu9999gu/kakaotalk-win32/archive/refs/heads/main.zip)하고 아래 순서로 진행합니다.

1. ZIP을 풀고 `SKILL.md`가 들어 있는 `kakaotalk-win32-main` 폴더 이름을 `kakaotalk-win32`로 바꿉니다.
2. 그 폴더 자체를 다시 ZIP으로 압축합니다. ZIP 안의 `kakaotalk-win32` 폴더 바로 아래에 `SKILL.md`가 있어야 합니다. [Claude 공식 패키징 가이드](https://support.claude.com/en/articles/12512198-how-to-create-custom-skills)
3. Claude에서 **Customize → Skills → + → Create skill → Upload a skill**로 이동해 ZIP을 업로드하고 켭니다. [Claude 공식 업로드 가이드](https://support.claude.com/en/articles/12512180-use-skills-in-claude)

이 경로는 Claude 계정에 스킬 지침을 등록하는 방법입니다. Chat과 Cowork의 코드 실행은 격리된 환경에서 이루어지므로, PC에서 실행 중인 카카오톡을 조작하려면 데스크톱의 **Code → Local**이나 Windows PowerShell을 사용하세요. [Claude Cowork 실행 환경](https://support.claude.com/en/articles/13345190-get-started-with-claude-cowork)

### 공통 실행 확인

선택한 앱의 등록 명령을 실행한 **같은 PowerShell 창**에서 이어서 실행합니다. `inspect`가 JSON에서 `"status": "inspected"`를 반환하면 카카오톡 창을 찾은 것입니다.

```powershell
python -m pip install -r "$skillDir\requirements.txt"
python -B -X utf8 "$skillDir\scripts\kakaoctl.py" inspect
```

`python` 명령을 찾지 못하면 Windows용 Python을 설치한 뒤 PowerShell을 다시 열고 `$skillDir`를 지정하세요. Python Launcher를 쓰는 경우 두 명령의 `python`을 모두 `py -3`으로 바꾸면 됩니다. `APP_NOT_UNIQUE`가 나오면 같은 Windows 세션에서 카카오톡 PC에 로그인했는지 확인하세요. 여러 카카오톡 프로세스가 잡히면 `--pid`로 대상을 지정할 수 있습니다. 자세한 명령은 [SKILL.md](SKILL.md)에 있습니다.

Git 없이 설치하려면 위 소스 ZIP을 풀고 폴더 이름을 `kakaotalk-win32`로 바꾼 뒤, Codex는 `%USERPROFILE%\.agents\skills`, Claude Code는 `%USERPROFILE%\.claude\skills` 아래에 폴더를 통째로 넣습니다. 해당 앱의 등록 블록에서 `$skillDir = ...` 줄을 실행하고 위 두 명령을 이어서 실행하세요.

이미 설치했다면 기존 폴더를 사용하세요. Git으로 설치한 폴더는 `git -C "$skillDir" pull --ff-only`로 갱신할 수 있습니다. ZIP으로 설치했다면 새 버전을 내려받아 기존 설치 폴더의 필요한 파일을 갱신하세요.

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
