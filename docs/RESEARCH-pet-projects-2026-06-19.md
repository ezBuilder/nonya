# Research: Desktop-Pet / Agent-Monitor Projects (28 repos / 32 findings)

Synthesis for **nonya** — a macOS top-of-screen "island" where a character's eyes peek out and watch; when it detects the Claude Code / Codex session is idle/slacking, the character descends from the top to the agent window and nudges it back to work. Target rig: photorealistic-ish 3D (three.js) in a transparent overlay, idle/walk/run/angry/scream/cheer state machine, Python detection/injection core.

Date: 2026-06-19. Sources: 32 per-repo findings.

---

## 1. Summary Table

| repo | stack | uiForm | watchMethod | intervention | character |
|---|---|---|---|---|---|
| rullerzhou-afk_clawd-on-desk | JS / Electron, koffi FFI | floating-window | hooks→localhost HTTP (ports 23333-37) + JSONL log-poll fallback | partial: permission-bubble decision relay (no inject) | 2d-sprite (crab) |
| rainnoon_oc-claw | Tauri v2 + React + Rust, objc2 | notch/island | hooks→Unix socket + JSONL/lsof + CGEvent idle | yes: permission relay + jump-to-terminal (no inject) | pixel / 2d-sprite |
| melisaliao502-debug_mr-krabs | Electron, koffi, AppleScript | floating-window | hooks→localhost HTTP + JSONL poll | yes: AppleScript clipboard-paste inject + autonomous `claude -p` queue | 2d-sprite (crab) |
| ntd4996_agentpet | Swift / SwiftUI + AppKit | menubar + floating-window | hooks→Unix socket + disk-queue fallback | display/notify only | 2d-sprite |
| usedhonda_clawgate | Swift / AppKit, SwiftNIO, AX, Vision OCR, whisper | menubar + floating-window | tmux capture-pane poll (TmuxDirectPoller 20s) + process-tree probe; cc-status-bar WS present but decoupled/rollback-only | yes: tmux send-keys re-prompt + auto-answer menus + capture-verify-retry (10×) | 2d-sprite (claw) |
| littlebearapps_untether | Python asyncio, aiohttp | other (Telegram bot) | JSONL stream parse + 0.5s CPU-aware liveness poll | yes: auto-continue + `claude --resume` loop re-fire | none (logo only) |
| graykode_abtop | Rust, ratatui | TUI | process scan + transcript/JSONL parse + statusLine hook | no (read-only); kill/jump only | none |
| ChanceYu_CoPet | Tauri/Rust + React, tauri-nspanel | floating-window + tray | hooks→loopback HTTP (token auth) + 100ms idle tick | none (display-only, never blocks) | 2d-sprite |
| fredruss_agent-paperclip | TS, Electron + React, chokidar | floating-window | Claude hooks→status.json + Codex JSONL tail | none (display-only) | 2d-sprite (PNG packs) |
| BinaryFroggy_Hopet | Swift / SwiftUI + AppKit | notch/island + floating + menubar | hooks→UDS (length-prefixed frames) + Codex JSONL poll | yes (bounded): permission/AskUser reply via blocked hook | 2d-sprite (seal) |
| pixel-agents-hq_pixel-agents | TS, VS Code ext + Fastify, Canvas 2D | VS Code panel webview | hooks→localhost (bearer) + JSONL poll fallback | none (can spawn terminal only) | 2d-sprite (office) |
| huangguang1999_pixel-agents | TS/React + Rust/Tauri, Canvas 2D | floating-window | hooks→Python shim→UDS + PID reaper (kill -0) | none (read-only) | 2d-sprite (office) |
| marciogranzotto_clawd-tank | Python + C (ESP32 firmware/SDL2) | external LCD / sim window + menubar | Claude hooks→Unix socket→daemon | none (passive display) | 2d-sprite (crab) |
| bcotrim_ai-companion | Swift / AppKit, Network.framework | floating-window | http hooks→loopback NWListener + decay timers | none (display-only) | 2d-sprite (Wapuu) |
| Geraldgan_codex-cc-pet | Swift / AppKit | floating-window | 3s state.json poll + 8KB log-tail | none (display + notify) | emoji |
| rrlan_mochi-pet | Swift / SwiftUI + AppKit | floating-window + menubar | 2s JSONL transcript poll + inbox.log poll | yes (partial): deep-link + osascript Return to confirm Allow | 2d-sprite (vector blob) |
| CalebLiu_diy-ai-desktop-pet | Swift / SwiftUI + AppKit | floating-window | hooks→loopback HTTP + CGEvent/IOKit/NSWorkspace | display + summon (raises window via AX) | 2d-sprite (AI-gen PNG) |
| Calior1029_NomiPetApp | Swift 6 + AppKit, sqlite3 CLI | floating-window + tray | 5s log/state-file poll + NSWorkspace focus | offer-help bubbles to human (no agent inject) | 2d-sprite (Nomi) |
| funAgent_ai-bubu | Tauri 2 + Rust + Vue, tauri-nspanel | floating-window + tray | 3s TOML-adapter poll (JSONL/sqlite/process/mtime) | none (passive) | 2d-sprite |
| alvinunreal_openpets | TS/Node + Electron, get-windows, MCP | tray + floating (confines to window) | hooks→IPC + MCP server | display + AXRaise focus (no agent inject) | 2d-sprite |
| IvanWng97_pixtuoid | Rust workspace, ratatui + winit | TUI + floating window | hook shim→socket + JSONL tail + liveness | none (strictly observe-only) | 2d-sprite (pixel office) |
| sotthang_so-agentbar | Swift / SwiftUI, SpriteKit | menubar + floating-window | FSEvents JSONL log parse + poll fallback | display + notify (no inject) | pixel (RPG sprites) |
| hoangsonww_Claude-Code-Agent-Monitor | Node/Express + React + Electron | browser floating + tray | Claude hooks→HTTP→WS bus | no auto-inject; user deep-link spawns new run | 2d-sprite (SVG cat) |
| acunningham-ship-it_agent-htop | Go, Bubble Tea | TUI | JSONL parse + fsnotify + poll | limited (Paperclip kill/pause via API); CC read-only | none |
| terrytan95_AgentBar | Swift / SwiftUI | menubar | state-file poll + ps process scan | indirect (Codex account rotation + restart) | none |
| scari_AgentBar | Swift 6 / SwiftUI, sqlite | menubar | Unix socket + 10s log-tail fallback | none (notify only) | none |
| lixiaocong_AgentBar | Swift 6 / SwiftUI, WidgetKit | menubar | timer poll of credential files + usage APIs | none (quota dashboard) | none |
| slopus_happy | TS monorepo, Expo/RN + Tauri, socket.io | other (full app window) | fetch-monkeypatch over fd 3 + transcript tail + SDK stream | yes: human remote-control prompt inject (not auto) | icon (identicon) |
| asheshgoplani_agent-deck | Go, Bubble Tea + tmux | TUI + web | hook files (~/.agent-deck/hooks) + tmux + poll | yes: tmux send-keys inject + autonomous "conductor" | none |
| abhay-byte_nexus | React + Rust/Tauri, xterm.js, portable-pty | floating-window | PTY stream parse + sysinfo (no idle detection) | none (passive host) | none |
| jeraldhu-yuan_lumi | TS/Electron + child agent | floating-window | wraps `claude -p ... stream-json` long-lived child + SDK AgentEvent stream + CGEvent idle | yes: `--append-system-prompt` persona/supervisor inject into its own child (orchestrator, not watchdog) | procedural (8-way gaze) |
| awen11123_usage-pet | Swift / AppKit, Core Graphics | floating-window + menubar | 2s JSONL poll (last-line `type` → thinking/working/idle) + last-8KB tail; vendor usage API | none (display-only) | procedural (13 skins, 4 moods) |

(28 distinct repos across 32 finding objects: usedhonda_clawgate, huangguang1999_pixel-agents, jeraldhu-yuan_lumi, asheshgoplani_agent-deck, and lixiaocong_AgentBar each appear as two finding objects keyed by different facets. Counts below.)

---

## 2. UI-Form Patterns

Tally by primary surface (some repos list multiple):

- **menubar (NSStatusItem-centric):** 6 — ntd4996_agentpet, terrytan95_AgentBar, scari_AgentBar, lixiaocong_AgentBar, sotthang_so-agentbar, plus menubar-as-secondary in usedhonda_clawgate / rrlan_mochi-pet / Calior1029_NomiPetApp / marciogranzotto_clawd-tank.
- **notch / island (top-of-screen):** **3** — rainnoon_oc-claw, BinaryFroggy_Hopet, and partially rullerzhou-afk_clawd-on-desk's "Mini Mode" edge-dock (not a true notch). Only oc-claw and Hopet truly sit in the macOS notch.
- **floating-window (free desktop pet):** the dominant form — ~13: clawd-on-desk, mr-krabs, CoPet, agent-paperclip, ai-companion, codex-cc-pet, mochi-pet, diy-ai-desktop-pet, NomiPetApp, ai-bubu, openpets, lumi (jeraldhu-yuan), huangguang1999_pixel-agents.
- **TUI:** 4 — graykode_abtop, IvanWng97_pixtuoid, acunningham-ship-it_agent-htop, asheshgoplani_agent-deck.
- **VS Code panel webview:** 1 — pixel-agents-hq_pixel-agents.
- **other (Telegram / full app window):** 3 — littlebearapps_untether, slopus_happy, abhay-byte_nexus.

### How the TOP-ISLAND / notch ones do it (directly relevant to nonya)

**rainnoon_oc-claw** — the closest working reference to nonya's "watch" half. Tauri v2, `decorations:false`, always-on-top mini window pinned at menu-bar level centered on the notch.
- Notch placement via **objc2 / objc2-app-kit**: `get_notch_offset` derives the offset from `safeAreaInsets`; the NSWindow `level` is set above the menu bar; `collapsed_x` centers beside the notch.
- Click-through via `set_ignore_cursor_events`.
- Collapsed = small strip beside the notch; expands on hover into a session-detail panel (an efficiency poll thread drives the hover-expand).
- Windows simulates the same top-center position above the taskbar with an outside-click poll watcher.
- oc-claw's own watch stack is itself a model, not just its placement: a 3-way hybrid of Unix-socket hooks + JSONL/`lsof +D` open-file-handle detection + `CGEventSourceSecondsSinceLastEventType` system idle + agent-PID exit detection (see §3.D). Treat it as the reference detect layer as well as the reference notch.

**BinaryFroggy_Hopet** — Swift/AppKit, essentially a Dynamic-Island product already.
- `NotchWindow` is a borderless **non-activating NSPanel pinned at `statusBar + 1` level** over the physical notch.
- Collapsed top bar animates open (0.42s easeInEaseOut) into expanded action cards when a session needs attention.
- `NotchDetector` handles real-notch vs fallback top-bar (non-notch Macs).
- Highest-priority-state aggregation across multiple sessions feeds the one collapsed-bar caption ("Thinking…/Permission needed/Waiting for your answer").
- App-side idle inference: a `ThinkingTimer` ticks every 500ms and **promotes a "responding" session to "thinking" after 8s** of no event, with a `CompletedDecayTimer` for decay — a reusable island idle-inference layer when events go quiet (see §5).
- `CodexVscodeSessionWatcher` polls `~/.codex/sessions` rollout JSONL **every 0.8s** to cover Codex's VS Code/Cursor extension, which doesn't run hooks.json — an island-relevant watch signal for extension-hosted agents (see §3.B).
- Note: the actual character/eyes live in a separate floating `PetWindow`, not the notch — the notch itself is text + chevron only.

Key takeaway for nonya: neither notch repo renders an animated *character* in the notch (Hopet keeps the seal in a separate floating panel). nonya's "eyes peeking from the island" rendering is novel territory — the placement math (`safeAreaInsets`/`get_notch_offset`, statusBar+1 NSPanel level, NotchDetector fallback) is the reusable part.

---

## 3. Watch & Detect Techniques Catalog

Every distinct detection mechanism observed, grouped:

### A. Lifecycle hooks (push)
Most common and most reliable. The agent CLI's hook config invokes a small helper on each event; the helper ships the event to the app.
- **Hook → localhost HTTP:** clawd-on-desk (multi-port 23333-37 + `~/.clawd/runtime.json` discovery + self-ID header), mr-krabs (127.0.0.1:23333), CoPet (random port, Bearer token, token-bucket rate limit, 16KB cap), agent-paperclip (writes status.json), ai-companion (NWListener 127.0.0.1:7387, hooks `timeout:1`, plus a 30s "staleness" decay tick as the Codex-style base timer that drives its decay sequence), diy-ai-desktop-pet (7777), pixel-agents-hq (Fastify, per-session bearer), hoangsonww (HTTP→WS bus).
- **Hook → Unix domain socket:** oc-claw (`/tmp/ooclaw-claude.sock`), agentpet (newline-delimited + disk-queue fallback), Hopet (`~/.hopet/run/hopetd.sock`, **length-prefixed UInt32-BE JSON frames**, synchronous request/response), clawd-tank (`~/.clawd-tank/sock`), huangguang1999 (Python shim stamps source+ppid → `~/.pixel-agents/bus.sock`), pixtuoid (200ms write timeout, **always exit 0**), scari_AgentBar (`~/.agentbar/events.sock` via `nc -U`), openpets (Unix socket / named pipe / TCP-for-WSL).
- **Hook → state file on disk:** agent-deck (`~/.agent-deck/hooks/{id}.json`; `sessionstatus.Derive` applies **per-tool freshness windows** — codex running=20s, waiting=2m; claude=2m — plus **acknowledged→idle folding** and a documented **tmux pane-title fallback when the hook is stale/absent**; a directly reusable "how long until a hook event goes stale" model), clawd-tank notify file.
- **statusLine hook (write-a-state-file):** abtop (`~/.claude/abtop-rate-limits.json`), agent-paperclip (statusLine wrapper for 5h rate-limit %, preserves pre-existing line).

### B. JSONL transcript / session-log parsing (pull)
The other workhorse; works for desktop apps and CLIs that don't fire shell hooks.
- **Tail newest `~/.claude/projects/**/*.jsonl` + `~/.codex/sessions/**/*.jsonl` by mtime:** mochi-pet (2s, growing=busy, >15s quiet=done), usage-pet (2s, last-line `type` field → thinking/working/idle), ai-bubu (3s, last `timestamp`), NomiPetApp (5s, +Codex sqlite), abtop, agent-htop, pixtuoid, so-agentbar, Hopet's `CodexVscodeSessionWatcher` (0.8s poll of `~/.codex/sessions` rollout JSONL to cover the VS Code/Cursor extension that doesn't run hooks).
- **Byte-offset incremental reads (only new bytes):** agent-paperclip (chokidar offset reads), so-agentbar (`fileOffsets` persisted in UserDefaults), Geraldgan_codex-cc-pet (last ~8KB), usage-pet (last 8KB).
- **FSEvents / fsnotify file-system watch (not polling):** so-agentbar (CoreServices FSEvents, 0.5s debounce + 30s fallback timer), agent-htop (Go fsnotify recursive), Hopet's `hopet-emit` (DispatchSource fsevents on transcript to grab last assistant message at Stop).
- **Pluggable adapter registry (TOML-driven):** ai-bubu — a **5-adapter system (sqlite / jsonl / process / file_mtime / vscode_ext)** where adding an AI tool is dropping a `.toml` file (no code), adapters run concurrently via `thread::scope` with a **5s probe timeout** and **automatic per-provider process-CPU fallback**. The most extensible watch-layer pattern in the set for supporting many agents.
- **tool_use ↔ tool_result pairing for stall detection:** agent-htop (`computeCurrentTask`: last tool_use with no matching tool_result + elapsed >30s while running = STALLED), abtop (unanswered tool_use = Executing).
- **Transcript-derived state correction (done→waiting):** agentpet's `QuestionDetector` — because Claude's Stop hook can't tell "done" from "ended the turn by asking a question," it inspects the latest assistant message (last-sentence question heuristics, ignoring polite "let me know if…" tails) and **corrects done→waiting before notifying**. Directly the "is the agent stuck asking, or genuinely idle?" disambiguation nonya needs.
- **False-positive scrubbing for failure detection:** NomiPetApp scrubs benign phrases ("0 errors" / "error handling" are NOT failures) and uses **exit-code-aware failure detection** to avoid crying wolf — a guard nonya's angry/scold trigger needs.
- **Status thresholds directly reusable for an island's eyes:** so-agentbar — bash long-running = 600s vs waiting-for-approval = 5s, 300s mtime = idle, extended-thinking dedupe, and **permissionMode-aware auto-approval** (readOnly / acceptEdits / bypassPermissions suppress false approval prompts).
- **SQLite DB reads:** NomiPetApp (`~/.codex/state_5.sqlite`), ai-bubu (Cursor `state.vscdb`), abtop (OpenCode `opencode.db`).

### C. Terminal/tmux scraping
- **tmux capture-pane + regex over pane tail:** usedhonda_clawgate (`TmuxDirectPoller`, 20s; classifies running vs waiting_input vs permission_prompt via `• Working(`, `❯` glyph, permission phrases; infers Claude-vs-Codex by walking the pane's descendant process tree for `@anthropic-ai/claude` etc.), agent-deck (tmux pane-title fallback + `IdleTimeoutWatcher` polling pane content for unchanged-content staleness — note its idle action is **destructive enforcement: stop/kill the session (`ReasonIdleTimeoutExpired`), not a re-prompt nudge** — do not conflate with re-prompting).

### D. Process / OS-level
- **Process-table scan for agent PIDs/CPU/ports:** abtop (`proc_pidinfo`/sysinfo/procfs; descendant CPU >5% = Executing; parses /proc/net/tcp, lsof, netstat for ports), terrytan95_AgentBar (`ps -axo command=` guard: "is codex actively working?").
- **`lsof +D` to find which .jsonl a live process holds open:** oc-claw (file handle held = agent working).
- **PID liveness / death detection (synthesize SessionEnd):** huangguang1999 (`kill -0` reaper every 30s), pixtuoid ("negative-vouch ledger": id missing from two healthy snapshots ≥60s apart = confirmed exit), clawd-on-desk (detect crashed/exited PIDs), clawd-tank (`process.kill(pid,0)`).
- **CPU-aware liveness stall (alive-but-thinking vs stuck):** untether — a `_subprocess_watchdog` that polls **every 0.5s for liveness stalls** and runs `is_cpu_active` **CPU-delta snapshots within that watchdog** to gate auto-kill (the CPU check runs inside the 0.5s loop, not as a separate loop) — avoids false "stuck" when the model is genuinely working. **Most robust idle-vs-busy heuristic in the set.**
- **OS user-idle / focus:** oc-claw + diy-ai-desktop-pet + lumi use `CGEventSourceSecondsSinceLastEventType`; diy-ai + NomiPetApp + CalebLiu use `NSWorkspace.frontmostApplication` / didActivateApplication + dev-app bundle-id whitelist to know if the user is even watching the agent window.

### E. Subprocess stdout / wrapper
- **Wrap the agent and read its stream-json stdout:** untether (`JsonlSubprocessRunner`), lumi (`claude -p ... stream-json` long-lived child), happy (`claude_local_launcher.cjs` **monkey-patches global.fetch and emits fetch-start/fetch-end over fd 3** — terminal-agnostic busy/idle, debounced 500ms), nexus (portable-pty pty-output/pty-exit).
- **Universal process-lifecycle wrapper for any CLI:** agentpet's `agentpet run -- <cmd>` wraps an arbitrary agent and emits working/done from process exit — a watch method that needs no hooks, no JSONL, no tmux.
- **SDK message stream:** happy remote mode (`system/init`→thinking, `result`→idle), lumi (AgentEvent stream from app-server JSON-RPC).

### F. API polling (quota, not activity)
- lixiaocong/scari/terrytan95 AgentBar, usage-pet — poll vendor usage endpoints + credential files on a timer. Not agent-activity detection.

### Most robust approaches
1. **Hooks + JSONL-tail dual-mode with graceful degradation** (pixel-agents-hq, clawd-on-desk, ai-companion): hooks give instant/reliable events; JSONL poll fills gaps and supplies tool-status text hooks don't carry. A per-agent flag disables the heuristic timers the instant a real hook arrives.
2. **CPU-aware liveness** (untether) to distinguish "thinking" from "stuck" — the single best signal for nonya's "slacking" trigger.
3. **PID-death / negative-vouch reapers** (pixtuoid, huangguang1999) to synthesize the SessionEnd that Codex never emits and clear stale "working" states.
4. **Non-blocking hook invariant** (CoPet, pixtuoid, openpets): helper always exits 0, fire-and-forget with ~0.2-0.8s timeout, so the watcher can never stall the agent.

---

## 4. Intervention / Injection Techniques

Most repos are display-only. The ones that actually act on the agent:

### Real prompt/keystroke injection into the agent
- **melisaliao502-debug_mr-krabs** — the strongest inject example. AppleScript walks the process tree to find the owning terminal app, **pastes the prompt via clipboard + Enter (`key code 36`)**, then restores prior focus and clipboard. Runs a two-tier proactivity cadence: (a) a fully autonomous **task executor** spawning headless `claude -p` (prompt via stdin) when the pet goes idle, on a 30s pending-task tick, and on a daily schedule, with confidence-graded delivery + reflection retry; and (b) a separate **context-monitor that *proposes* 1–3 `[?]` tasks every 4h** (first run +10min) which do NOT auto-execute. The propose-vs-act split is directly relevant to gating nonya's descend-to-scold cadence.
- **usedhonda_clawgate** — `<cc_task>...</cc_task>` XML re-prompts injected into the tmux pane via `tmux send-keys` (with a `[from:OpenClaw Agent]` prefix); `<cc_answer project=..>N</cc_answer>` auto-selects numbered AskUserQuestion options. A remote OpenClaw AI reviews `/v1/events` SSE and replies. Also auto-injects Up/Down/Enter keystrokes to pick the recommended menu option locally in "auto" mode. Crucially, after each `<cc_answer>` it **re-captures the pane up to 10×** to drive multi-step AskUserQuestion wizards — an inject → re-capture → verify → re-inject loop that is the model for *confirming a nudge actually landed*.
- **asheshgoplani_agent-deck** — `agent-deck session send <id> "msg"` injects via tmux `send-keys -l` (literal) + Enter, with a 60s readiness wait and send-guards. An autonomous **"conductor"** (a real Claude Code session fed `[HEARTBEAT]` digests of waiting sessions) either auto-responds when confident or escalates `NEED:` items to phone (Telegram/Slack/Discord). Never sends to *running* sessions; never auto-responds destructively.
- **littlebearapps_untether** — auto-continue: relaunches/`claude --resume <id>` when a Claude session exits prematurely, re-issuing the original prompt as a fresh user turn (bounded by cost/iteration/duration caps). Intercepts Claude's own CronCreate/ScheduleWakeup tool_use events at the JSONL layer to re-fire loops after subprocess death.
- **slopus_happy** — pushes phone/web-typed messages into the live Claude Agent SDK `query()` iterable; queued remote message auto-switches local→remote. Human-driven, not autonomous.
- **lumi (jeraldhu-yuan)** — injects persona/supervisor context via `--append-system-prompt` into its own child agent; orchestrator, not a watchdog.

### Permission-decision relay (answer the agent's prompt, no free-text inject)
- **rullerzhou-afk_clawd-on-desk**, **rainnoon_oc-claw**, **BinaryFroggy_Hopet** — blocking hook holds the agent paused on a socket/HTTP until the user clicks Allow/Deny/Auto in the GUI; the decision (`{behavior:"allow"|"deny"}`) is written back as the agent's actual decision. Works across all terminals because it never touches the TTY. Hopet also handles AskUserQuestion elicitation (`{updatedInput:{...,answers}}`).
- **oc-claw's `auto_approve` is a true session-level bypass, not a per-prompt answer.** Its `resolve_claude_permission` relay can return `auto_approve`, which does `setMode bypassPermissions` for the *session* — flipping the agent into no-prompt mode persistently rather than answering one prompt. This is the one repo showing how the island could keep *future* nudges from being blocked by permission prompts (relevant to making a descend-to-scold loop actually go through unimpeded).

### Soft / indirect
- **rrlan_mochi-pet** — detects pending-permission from the transcript tail, opens the **session deep-link (`claude://resume` / `codex://threads`)** to foreground the *exact* session, then synthesizes a Return keypress (osascript `key code 36`) to click "Allow" — gated on Accessibility (`AXIsProcessTrusted`). Confirms approval only; no free-text. The deep-link half is a cleaner "descend to the specific agent" path than PPID-walking for the desktop-app case (see §6).
- **Window-focus only (no inject):** oc-claw (`jump_to_claude_terminal`), CalebLiu_diy (AXUIElementPerformAction raise + holds "done" celebration until user refocuses dev app), openpets (PPID-chain terminal discovery → occlusion check → `AXRaise` via osascript), so-agentbar / NomiPetApp (tappable notification opens editor).
- **Process-level:** terrytan95_AgentBar (account rotation + `pkill -x Codex` restart, guarded by a ps "is it working?" check), agent-htop (Paperclip kill/pause via API, dry-run-first policy engine).

### The injection mechanics, ranked for nonya
1. **tmux send-keys** (clawgate, agent-deck) — cleanest if the agent runs in tmux; literal keys + Enter, no clipboard clobber, terminal-agnostic within tmux.
2. **AppleScript clipboard-paste + Enter** (mr-krabs) — works for non-tmux terminals and the desktop app; must save/restore clipboard and prior focus; needs the process-tree→terminal mapping.
3. **`claude --resume` / SDK query re-issue** (untether, happy) — re-prompts the agent process directly without touching any window; best when nonya owns/launches the session.
4. **Blocking-hook decision relay** (Hopet/oc-claw) — only answers permission/AskUser, not a free-text "get back to work" nudge.

---

## 5. Character / Animation Approaches

- **2d-sprite (spritesheet or PNG-frame):** the overwhelming majority. Common format = the open **Codex/Petdex "hatch-pet" pack** (pet.json + spritesheet, nominally 8 cols × 9 rows of 192×208 cells), rendered by stepping CSS `background-position` or a per-cell view: oc-claw, agentpet, CoPet, ai-companion, NomiPetApp, and others. State→row mapping (idle / run-left/right / waving / jumping / failed / waiting / running / review) is near-standard.
- **pixel-art office "people" with pathfinding/roaming:** pixel-agents-hq, huangguang1999_pixel-agents, IvanWng97_pixtuoid — each agent = a character on a Canvas/terminal game loop with A*/BFS pathfinding (walk to desk = TYPE, walk to bookshelf = READING/Read tool, sofa/lounge when idle >60s), sub-agents spawn as linked characters. sotthang_so-agentbar uses SpriteKit RPG sprites wandering a "rest zone."
- **Procedural / code-drawn (no assets):** rrlan_mochi-pet (SwiftUI vector blob with squash-stretch, blink, hop), awen11123_usage-pet (Core Graphics, 13 skins, 4 moods), hoangsonww (hand-authored inline SVG cat).
- **emoji fallback:** Geraldgan_codex-cc-pet (🐤 bob), ai-companion's "Cat" fallback.
- **icon / identicon:** slopus_happy (8×8 hashed grid).
- **none:** abtop, agent-htop, agent-deck, nexus, terrytan95/scari/lixiaocong AgentBar (status dots/bars/glyphs only).
- **No 3D-mesh / Live2D-rig rendering anywhere in the set.** The most game-engine-like are **SpriteKit** (so-agentbar: `PixelCharacterNode`/`PixelAgentsScene`, a 2D scene-graph game engine) and the Canvas-2D game loops in the pixel-office repos — all still 2D. nonya's photorealistic three.js *mesh* rig is unique: zero prior art to copy for the renderer; only the *state machine* and *motion vocabulary* transfer.

### Eye-tracking / gaze (directly relevant to nonya's "peeking eyes")
- clawd-on-desk: idle SVG with eye tracking + body lean + shadow stretch; polls `screen.getCursorScreenPoint()` to follow the cursor; 60s OS-idle → sleep, wake-on-move.
- mr-krabs, NomiPetApp: idle "follow" sprite tracks the mouse.
- hoangsonww (Tabby): pupils track the real cursor via rAF; **module-level `lastCursor` captured before mount** so the pet aims correctly on its very first frame.
- lumi: cursor-proximity gaze via `atan2`→8-way `GazeDirection`.

### Movement / roaming / descent
- lumi: "curiosity flights" + wander targets via `window.setFrameOrigin` — closest existing thing to a character flying around the desktop.
- usedhonda_clawgate: a 0.1s `windowTrackingTimer` reads frontmost app + AX focused window and **docks Chi to the active window's edge**, with hide-claw / peek / emerge animations (claw retracts to screen edge and peeks back) — conceptually nonya's "descend then cling."
- alvinunreal_openpets: `confinement-manager` pins the pet to a specific window's bounds.
- pixel office repos: A*/BFS pathfinding; characters "walk in" on session start, "burrow/walk out" on end (clawd-tank, pixtuoid).

### State machines worth copying
- **Two-layer state machine (expression + locomotion):** clawgate's Chi renders agent/system state through a split of **face/expression** and **body/locomotion** layers — the closest analog to nonya's separation of face emotion (angry/cheer) from body motion (descend/walk/run). Copy the two-layer split directly.
- **Priority aggregation:** "needs your input" / waiting / alert **outranks** busy work, collapsed to one highest-priority indicator across N sessions (clawd-tank, Hopet, ai-companion's single rolled-up DisplayState).
- **Idle decay sequence:** yawn→doze→sleep (mr-krabs); working→idle (3min)→asleep (10min)→waiting (30min) decay timers driven by a 30s base "staleness" tick (ai-companion); Hopet's `ThinkingTimer` **promotes "responding"→"thinking" after 8s of silence** (500ms tick) with a `CompletedDecayTimer` — an app-side inference for when events go quiet; ActiveHigh/Medium/Low/Inactive→Idle/Walk/Run/Sprint by seconds-since-last-event, with a **45s cooldown bridge** to survive tool-call gaps (ai-bubu's `scoring.rs`).
- **Min-display debounce** so states don't flicker (mr-krabs, happy's 500ms).

---

## 6. Best Ideas to Steal for nonya (eyes-in-island → descend-to-scold)

Mapped to concrete repos and files/mechanisms:

**Island placement (the "peek from the notch" frame)**
- Steal oc-claw's **objc2 `get_notch_offset` from `safeAreaInsets`** + NSWindow level above menu bar + `collapsed_x` centering. (rainnoon_oc-claw)
- Steal Hopet's **non-activating NSPanel at `statusBar+1` level** + `NotchDetector` real-notch-vs-fallback-top-bar so non-notch Macs still get a top bar. (BinaryFroggy_Hopet)
- nonya difference: render the 3D eyes *in* the island — no prior repo does this, so this is net-new.

**Watch layer (the "eyes that watch")**
- Default to **hooks + JSONL-tail dual-mode** with the per-agent "disable heuristics once a real hook arrives" flag. (pixel-agents-hq, clawd-on-desk, ai-companion)
- Add **CPU-aware liveness** (`is_cpu_active` CPU-delta snapshots, 0.5s) to tell "thinking" from "slacking/stuck" — this is the precise trigger for nonya's descent. (littlebearapps_untether)
- Add a **PID-death reaper / negative-vouch ledger** to clear stale "working" and synthesize the SessionEnd Codex omits. (pixtuoid, huangguang1999)
- Use **tool_use-without-tool_result for >Ns = stalled** as the "slacking" signal when no hook fires. (acunningham-ship-it_agent-htop)
- Steal the concrete **no-hook fallback constants**: `PERMISSION_TIMER_DELAY_MS = 7s` (non-exempt tool started, no completion ⇒ likely waiting on permission) and `TEXT_IDLE_DELAY_MS = 5s` (silence on a text-only turn ⇒ turn done), with `system/turn_duration` as the definitive turn-end. (pixel-agents-hq)
- Steal so-agentbar's **status thresholds** for the eyes: bash long-running = 600s vs waiting-for-approval = 5s, 300s mtime = idle, extended-thinking dedupe, permissionMode-aware auto-approval. (sotthang_so-agentbar)
- Recover the **"is the agent stuck asking vs genuinely idle?"** signal with a transcript-last-sentence `QuestionDetector` that corrects done→waiting before triggering. (ntd4996_agentpet)
- For multi-agent support, build the watch layer as a **TOML-driven pluggable adapter registry** (sqlite/jsonl/process/file_mtime/vscode_ext, concurrent via thread::scope, per-provider CPU fallback, 5s probe timeout) so adding an agent is a config file, not code. (funAgent_ai-bubu)
- Use a **per-tool freshness-window model** (codex running=20s/waiting=2m, claude=2m) + acknowledged→idle folding to decide when a hook event has gone stale. (asheshgoplani_agent-deck)
- Keep the hook helper **non-blocking (always exit 0, ~0.2-0.8s timeout)** so nonya never stalls the agent. (CoPet, pixtuoid, openpets)
- For the eyes: cursor follow via `screen.getCursorScreenPoint()` (clawd-on-desk) and pre-mount `lastCursor` capture for first-frame aim (hoangsonww).

**Trigger gating (when to descend — avoid being annoying)**
- "needs-input / waiting **outranks** busy" priority resolution across multiple sessions. (clawd-tank, Hopet)
- NomiPetApp's **ProactivityEngine**: weighs idle time + frontmost-app focus duration + late-night + a **per-hour budget** scaled by personality → stayQuiet / offerHelp / gentleCheckIn. Reuse directly so nonya descends sparingly.
- terrytan95_AgentBar's **`ps`-guard**: only intervene if the agent is *truly* idle/safe (regex over `ps -axo command=`), so nonya doesn't scold mid-work.
- openpets' **window-occlusion check** (`window-occlusion.ts`): only descend/scold when the agent terminal is *occluded or unfocused* (user not watching) — the missing "is the user actually looking" gate that prevents nonya from nagging while the user is already on it.
- NomiPetApp's **benign-phrase scrubbing + exit-code-aware failure detection** ("0 errors" / "error handling" are NOT failures) so the angry/scold trigger doesn't cry wolf.

**Descend + locate the target window**
- openpets: **PPID-chain terminal discovery** (`window-chain.ts findTerminalPidInChain`) + `get-windows`/CGWindowList for bounds + **occlusion check** + `AXRaise` focus — app-agnostic across Ghostty/iTerm/Terminal/VS Code/Warp. This is exactly how nonya finds *where* to descend.
- clawgate: 0.1s window-tracking timer docking to the active window's edge + hide/peek/emerge animation — the "cling to the agent window then re-emerge" beat.
- CalebLiu_diy: `NSWorkspace` focus-observer + dev-app bundle-id whitelist to know if the user is watching, plus per-pixel alpha hit-testing for click-through overlay.
- mochi-pet: for the desktop-app case, a **session deep-link (`claude://resume` / `codex://threads`)** foregrounds the *exact* session — a cleaner locate-by-deeplink path than PPID-walking when the agent is a GUI app.

**Scold / nudge (the actual intervention)**
- If the agent is in tmux: **tmux `send-keys -l` + Enter** with a 60s readiness wait + send-guards. (agent-deck) Cleanest, no clipboard clobber.
- Otherwise: mr-krabs' **process-tree→terminal mapping + AppleScript clipboard-paste + `key code 36`**, saving/restoring clipboard and focus.
- If nonya launches the session itself: **`claude --resume` re-issuing a fresh user turn** (untether) or SDK `query()` injection (happy) — no window targeting needed.
- After injecting, **verify it landed with a capture-verify-retry loop** — re-capture the pane (up to ~10×) and re-inject if the scold didn't take, exactly as clawgate drives multi-step wizards. (usedhonda_clawgate)
- To keep *future* nudges from being blocked by permission prompts, consider oc-claw's **session-level `setMode bypassPermissions`** (via the `auto_approve` relay) rather than answering each prompt one at a time. (rainnoon_oc-claw)
- For permission prompts specifically: **blocking-hook decision relay** so the island answers Allow/Deny without touching the TTY. (Hopet UDS length-prefixed frames; oc-claw allow_all/bypassPermissions)

**Animation / state machine**
- Idle decay sequence + 45s cooldown bridge (ai-bubu `scoring.rs`, mr-krabs yawn→doze→sleep) — maps onto nonya's idle/walk/run/angry/scream/cheer.
- Pure event→mood reducer separated from the render shell (hoangsonww Tabby `brain.ts`) — testable, liftable.

---

## 7. Gaps — What NONE of Them Do (nonya's whitespace)

1. **A character living inside the notch/island.** The two notch repos (oc-claw, Hopet) render only text/chevron in the notch; Hopet's actual creature sits in a *separate* floating panel. No project renders an animated character — let alone peeking eyes — in the island itself.
2. **3D-mesh / photorealistic rendering.** No 3D mesh and no Live2D rig anywhere. The most game-engine-like prior art is 2D only — SpriteKit (so-agentbar) and Canvas-2D game loops (pixel-office repos); every character is a 2D sprite, pixel-art, procedural vector, emoji, or icon. nonya's photorealistic three.js *mesh* rig has no prior art.
3. **A character that physically descends from the top to the agent window.** The closest is clawgate docking Chi to the *focused* window edge (any window, not specifically the agent) and openpets confining to a discovered terminal. None animate a top-island character *traveling down* to the agent and acting there — the visible creature and the intervention are always decoupled (clawgate nudges headlessly via tmux while the sprite just docks).
4. **Autonomous free-text "get back to work" re-prompt triggered by idle/slacking.** Injection exists (mr-krabs, clawgate, agent-deck, untether) but it's either human-driven, permission-only, confidence-gated conductor logic, or auto-continue-on-exit. None watch for *slacking* and autonomously inject a corrective nudge — and none tie that injection to a descending on-screen character. This combined behavior (slack detection → character descends → injects a scold) is nonya's core differentiator.
5. **Coupling the visible avatar to the intervention.** Universally, repos that inject do it headlessly (off-screen tmux/AppleScript/SDK) while repos with expressive characters never inject. nonya's value is making the *same* entity both the watcher and the actor, on screen.
6. **An "angry/scold" affective register.** Existing emotional states are happy/celebrate/worried/confused/sleeping. nonya's angry/scream/cheer escalation tied to how long the agent has slacked is unrepresented.
