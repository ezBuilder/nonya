# nonya 동작 방식

이 문서는 "노냐가 실제로 무엇을 보고, 언제 키를 보내고, 언제 아무것도 하지 않는지"를 설명합니다.

## 실행 상태

- 앱을 실행한 것만으로는 감시 코어가 자동으로 시작되지 않습니다. 메뉴바 앱과 눈 UI만 떠 있습니다.
- 메뉴에서 **감시 시작**을 누르면 번들된 `nonya` 코어가 `--all`로 실행되고 실제 감시가 시작됩니다.
- **자율모드** 체크는 시작 스위치가 아니라 동작 방식 설정입니다. 감시가 켜졌을 때 `--mode auto`로 입력대기까지 처리할지 정합니다.
- 예외적으로 Claude hook에 `NONYA_AUTOSTART=1`을 켰거나 launchd 에이전트를 설치/로드한 경우에는 버튼 없이도 코어가 시작될 수 있습니다.

## 감시는 화면 캡처가 기본이 아닙니다

nonya의 기본 감시는 화면을 계속 찍어서 보는 방식이 아닙니다.

1. Claude/Codex의 JSONL transcript, Antigravity 로그, 지정 transcript 파일을 읽습니다.
2. 파일의 최신 기록과 idle 시간을 보고 `working`, `idle`, `waiting`, `stuck`, `rate-limited`, `done` 같은 상태로 분류합니다.
3. 문제가 생긴 세션마다 "안전하게 같은 대상에 보낼 수 있는가"를 먼저 판단합니다.
4. 대상이 확실하면 nudge를 보내고, 불확실하면 알림과 ledger 기록만 남깁니다.

## CLI + tmux

가장 안전하고 권장하는 경로입니다.

- `tmux list-panes`와 프로세스 트리를 보고 Claude/Codex CLI가 들어 있는 pane을 찾습니다.
- 가능한 경우 session cwd로 pane을 먼저 특정하고, 실패하면 engine 프로세스 매칭으로 찾습니다.
- 후보가 없거나 둘 이상이면 추측하지 않습니다.
- pane이 copy-mode/pager 상태면 키를 보내지 않습니다.
- 주입은 `tmux send-keys -l -t <pane>` + Enter로 처리합니다.

이 방식은 화면, 앱 포커스, 마우스 좌표, 사용자의 키보드 상태에 의존하지 않습니다. 창이 다른 창 뒤에 있거나 화면에서 보이지 않아도 pane id만 맞으면 동작합니다.

## 일반 터미널(raw split)

tmux 없이 Terminal, Ghostty, iTerm 같은 터미널 split에 직접 넣는 방식은 기본적으로 자동복구에서 제외됩니다.

이유는 macOS에서 AX로 특정 split을 찾더라도 `CGEvent.postToPid` 키 이벤트가 선택한 AX split이 아니라 앱의 실제 활성 split으로 들어갈 수 있기 때문입니다. 라이브 검증에서 오주입 위험이 확인되어 기본값은 알림-only입니다.

연구용으로만 `NONYA_AX_SPLIT=1`을 켤 수 있지만, 일반 사용 경로로 권장하지 않습니다.

## Claude/Codex GUI 앱

GUI 앱은 훨씬 보수적으로 다룹니다.

### 사용자가 작업 중일 때

- 사용자의 HID idle 시간이 짧으면 "사용자가 앞에 있다"고 봅니다.
- 이때는 앱을 앞으로 가져오지 않습니다.
- 감시 대상 대화가 현재 frontmost 대화라고 판단될 때만 제한적으로 처리합니다.
- 아니면 알림만 보냅니다.

### 사용자가 자리를 비웠을 때

사용자 입력이 일정 시간 없으면 unattended 상태로 보고, GUI 복구를 시도할 수 있습니다.

1. 대상 앱을 앞으로 가져옵니다.
2. ScreenCaptureKit으로 해당 앱 창을 캡처합니다.
3. Apple Vision OCR로 사이드바와 헤더 텍스트를 읽습니다.
4. 세션 title, cwd, alias, 한글 romanization/fuzzy score로 맞는 대화 row를 고릅니다.
5. 후보가 없거나 애매하면 중단합니다.
6. 확실한 row만 실제 좌표 클릭으로 선택합니다.
7. composer에 nudge를 붙여넣습니다.
8. 다시 캡처/OCR로 텍스트가 composer에 들어갔는지 확인합니다.
9. Enter를 보내고, 다시 캡처/OCR로 composer에서 텍스트가 사라졌는지 확인합니다.

이 경로는 Computer Use처럼 별도 가상 커서를 쓰는 구조가 아닙니다. 실제 macOS 창을 앞으로 가져오고 실제 마우스/키보드 이벤트를 사용합니다. 그래서 사용자 작업과 완전히 분리되지 않으며, 타깃을 증명하지 못하면 아무 키도 보내지 않는 쪽을 선택합니다.

## 권한

- transcript 기반 감시는 별도 화면 권한이 필요 없습니다.
- tmux 주입은 tmux 접근만 필요합니다.
- GUI 앱 OCR은 Screen Recording 권한이 필요합니다.
- GUI 앱 클릭/붙여넣기/Enter는 Accessibility 권한이 필요합니다.

## 안전 원칙

- 같은 대상임을 증명하지 못하면 키를 보내지 않습니다.
- secrets, billing, 삭제, 설치, 외부 네트워크, 권한 상승, production, deploy, publish, release 같은 작업은 자율모드에서도 자동 승인하지 않습니다.
- GUI 앱보다 CLI + tmux가 우선 경로입니다.
- raw terminal split은 기본적으로 알림-only입니다.
