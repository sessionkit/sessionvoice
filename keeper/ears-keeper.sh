#!/usr/bin/env bash
# ears-keeper.sh: keep the listen capture on a device that comes and goes.
#
# The ears support losing the AirPods and getting them back, and an agent is
# notified when there is an audio device change. Without this the AirPods
# leave (shower, phone, Teams), the capture dies in 5 s, and nothing brings
# it back.
#
# Usage: ears-keeper.sh [DEVICE|default]
#   DEVICE   pin the ears to one input (a case-insensitive substring of its
#            name, as `ears.listen capture --device` takes it).
#   default  FOLLOW macOS's default input: the ears just follow the
#            default, with no device pinned.
#            AirPods out -> the laptop mic;
#            AirPods back -> the AirPods. A virtual default input (BlackHole,
#            Microsoft Teams Audio) is never followed: the ears take the
#            built-in mic, and a cable or a multi-output that has lost its
#            AirPods moves the Mac's default output to the built-in speakers
#            (EARS_OUTPUT_FALLBACK=0 turns the output half off).
#            In this mode ears.json (beside this script) is read every loop:
#              listen: false -> stop EVERY ear, start none, until true (read
#                in pinned-DEVICE mode too; the listen region says what stops)
#              input: "default" follows the Mac; a name pins the ears to it
#              room_on_call: true -> keep the room mic on during a call
#                (default: OFF while any call is live - call_live, security)
#            It is set from the phone (the Notifications webxdc app:
#            dynamic configuration, live).
#            No file = listen, follow the default.
#
# Every INTERVAL seconds it reads macOS audio INPUTS and the default input from
# input-watch's state file: one long-lived CoreAudio listener (input-watch.swift,
# built here on first run), so the loop itself never touches CoreAudio. Before
# it, the loop ran system_profiler every second, which cost
# coreaudiod ~17% of a core all night (measured).
# If the watcher cannot be built or keeps dying, it polls system_profiler as
# before. Then:
#   - any change to the input set -> a `device-change` event in the heard log
#   - the target device appears or disappears -> an EOM mail to $EARS_NOTIFY,
#     so the pager wakes the agent. EARS_NOTIFY unset: the operator, when
#     `session operator` names one (read once, at start). Set but
#     empty (EARS_NOTIFY=""): mail nobody, operator or not. Nobody to mail: the
#     log says so
#   - the target is present for STABLE checks in a row and no capture is
#     running -> it starts one (new STOP file), and mails that too
# PINNED mode never stops a capture: one whose device leaves exits on its own
# ("no audio for 5s"). FOLLOW mode stops the running capture (via its own
# --stop-file) when the default has settled on a DIFFERENT device, then starts
# one on the new default. Long-lived: run it with Bash run_in_background.
#
# The call ears are this job's too (merged: one ears keeper job, not
# ears-room + ears-call). The keeper runs keeper/call-ears
# as its child, an ear per live Delta call, and starts it again if it dies (2 s, doubling to
# 5 min; back to 2 s after a run of a minute). It stands aside while another job runs a
# call-ears (the old com.voicemode.ears-call), so a cutover never puts two ears on a call.
# EARS_CALLS=0: the room only. The call ears' own stop file still works: touch
# $CALL_EARS_DIR/STOP (default ${XDG_STATE_HOME:-~/.local/state}/ears/call-ears/STOP); rm it and
# the keeper starts them again.
set -uo pipefail
DEVICE="${1:-airpods}"
# 1 s x 2 checks: the AirPods-in switch took 12 s at 5 s x 3 (seen, then
# moved 12 s later); it should follow quickly.
# One loop costs ~0.35 s (system_profiler 0.25 s, three ears.json reads).
INTERVAL="${EARS_INTERVAL:-1}"
STABLE="${EARS_STABLE:-2}"
# The capture and the heard log are ears' own since 2030-06-09 (ears.listen,
# ears.heard). EARS_PY is a python that can `import ears` - by default the
# package's own venv beside this script (<root>/.venv, as `uv run --project`
# and the plugin's bin/ shims make it). EARS_W, the old way (a VoiceMode checkout
# to run voice_mode.listen from), is gone: set, it is ignored, and
# the log says so once. A voice_mode.listen capture already running is still
# adopted, never doubled (CAPTURE_PAT below).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
W="$ROOT"; PY="${EARS_PY:-$ROOT/.venv/bin/python}"; MOD="ears.listen"
if [ -n "${EARS_W:-}" ]; then
  echo "$(date +%H:%M:%S) EARS_W is set but ignored: the capture is ears.listen, run by $PY"
fi
[ -x "$PY" ] || { echo "ears-keeper: no python at $PY - run \`uv sync --extra capture\` in $ROOT, or set EARS_PY" >&2; exit 2; }
HEARD="$PY -m ears.heard"
# Who the device/capture mails go to. No name of ours
# (a host once defaulted to an agent's name). EARS_NOTIFY SET wins, and set but EMPTY
# means mail nobody: that is how a configured host opts out. UNSET: the operator's
# address, asked of the one reader (ears.operator_config, which runs `session
# operator`); it is empty unless an operator block is configured (privileged
# non-empty), so AGENT_OPERATOR_ADDR alone mails nobody. Read once, here: a change
# takes effect at the next keeper start.
# Nobody to mail: the keeper log still notes every event (mail_me).
if [ -z "${EARS_NOTIFY+set}" ]; then
  EARS_NOTIFY="$("$PY" -m ears.operator_config address 2>/dev/null || true)"
fi
NOTIFY="${EARS_NOTIFY:-}"
# Paths: settings in the voice settings folder, VOICE_SETTINGS (a copy of
# ears.home.SETTINGS; tests/test_settings_dir.py holds it to the module),
# logs in ~/Library/Logs/<thing>/: ~/Library/Logs/ears, as `ears services` (src/ears/
# services.py LOGS) has it (a host may set EARS_LOG_DIR to keep
# another log dir). RUN (stop files, the input-watch binary
# and state) has no fixed home yet: it defaults beside the package's other assets
# in ~/.cache/ears, and EARS_RUN moves it.
VOICE_SETTINGS="$HOME/.sessionkit/voice/settings"
CONF="${EARS_CONF:-$VOICE_SETTINGS/ears.json}"
LOGDIR="${EARS_LOG_DIR:-$HOME/Library/Logs/ears}"
RUN="${EARS_RUN:-$HOME/.cache/ears/run}"
mkdir -p "$LOGDIR" "$RUN"
SILERO="${EARS_SILERO_ONNX:-$HOME/.cache/ears/silero_vad.onnx}"   # where `ears install` puts it (src/ears/vad.py)
WATCH_SRC="$(cd "$(dirname "$0")" && pwd)/input-watch.swift"
WATCH_BIN="${EARS_WATCH_BIN:-$RUN/input-watch}"
WATCH_STATE="$RUN/input-watch.state"
WATCH_PIDF="$RUN/input-watch.pid"
CALLS_PIDF="$RUN/call-ears.pid"
watch_pid=""; watch_fails=0
FOLLOW=0; [ "$DEVICE" = default ] && FOLLOW=1

watching() { [ -n "$watch_pid" ] && kill -0 "$watch_pid" 2>/dev/null && [ -s "$WATCH_STATE" ]; }
watch_up() {  # keep input-watch running; call from the loop itself, never inside $(...)
  watching && return 0
  [ "$watch_fails" -ge 3 ] && return 1   # gave up: system_profiler from here on
  if [ -n "$watch_pid" ]; then note "input-watch $watch_pid died; restarting it"; fi
  if [ ! -x "$WATCH_BIN" ] || [ "$WATCH_SRC" -nt "$WATCH_BIN" ]; then
    note "input-watch: building $WATCH_BIN"
    if ! swiftc -O -swift-version 5 -o "$WATCH_BIN" "$WATCH_SRC" >&2; then
      watch_fails=3; note "input-watch: build FAILED; polling system_profiler"; return 1
    fi
  fi
  rm -f "$WATCH_STATE"   # never $AUDIO_DEVICES: a dead writer's file is told apart by its pid
  mkdir -p "$(dirname "$AUDIO_DEVICES")"   # not ${AUDIO_DEVICES%/*}: a slash-free path would mkdir the file's own name
  "$WATCH_BIN" "$WATCH_STATE" --json "$AUDIO_DEVICES" >/dev/null 2>&1 &
  watch_pid=$!; echo "$watch_pid" >"$WATCH_PIDF"
  for _ in $(seq 20); do [ -s "$WATCH_STATE" ] && break; sleep 0.1; done
  if watching; then note "input-watch: up, pid $watch_pid"; return 0; fi
  watch_fails=$((watch_fails + 1)); note "input-watch: no state after 2 s (fail $watch_fails of 3)"; return 1
}
watch_down() { [ -n "$watch_pid" ] && kill "$watch_pid" 2>/dev/null; rm -f "$WATCH_PIDF"; }
inputs() {  # one input device name per line, sorted
  if watching; then tail -n +2 "$WATCH_STATE"; return; fi
  system_profiler SPAudioDataType | awk '
    /^        [^ ].*:$/ { name = substr($0, 9, length($0) - 9); next }
    /Input Channels/ && name != "" { print name; name = "" }' | sort -u
}
default_input() {
  if watching; then head -1 "$WATCH_STATE"; else SwitchAudioSource -c -t input 2>/dev/null; fi
}
conf() {  # "LISTEN INPUT" from ears.json: "1 default" when absent or unreadable
  python3 -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
print(0 if d.get("listen") is False else 1, (d.get("input") or "default").strip() or "default")' "$CONF"
}
# --- decisions: pure, args in and a name out (tests/test_keeper_fallback.py runs them alone) ---
# Once the AirPods left and macOS made BlackHole 16ch
# the default input - a device call-tap had chosen by name the night before
# (docs/call-tap.md), where ~60 earlier drops had all fallen to the laptop mic.
# FOLLOW mode followed it and the ears heard nothing for 12 minutes. The output
# was the multi-output "AirPods + BlackHole 2ch", which with the AirPods gone
# plays only into the cable. A virtual cable is never the room, so FOLLOW falls
# back to the built-in mic and speakers. An ears.json input pin wins over all of
# it: the call-tap flow pins the ears to the AirPods before it moves the Mac's
# defaults onto the cables, so while pinned the keeper leaves the output alone.
# Virtual is known by NAME, not Transport: the loop never runs system_profiler.
VIRTUAL_PAT="${EARS_VIRTUAL_PAT:-^(blackhole( .*)?|microsoft teams audio)$}"   # grep -iE
# The built-in mic is known by TRANSPORT: input-watch writes each
# input's transport into devices.json, and `builtin` is the Mac's own mic whatever the model
# calls it. The old literal, "MacBook Pro Microphone", named nothing on an Air ("MacBook Air
# Microphone"), so the fallback above led nowhere there. When more than one input is `builtin`
# (a headset on the jack), the name pattern picks among them; while devices.json is stale it
# stands in alone. EARS_BUILTIN_MIC, when set, wins over both.
BUILTIN_MIC="${EARS_BUILTIN_MIC:-}"
BUILTIN_PAT="${EARS_BUILTIN_PAT:-^MacBook .*Microphone$|^Built-in Microphone$}"   # awk ERE
BUILTIN_OUT="${EARS_BUILTIN_OUT:-MacBook Pro Speakers}"
is_virtual() { [ -n "$1" ] && grep -qiE -- "$VIRTUAL_PAT" <<<"$1"; }
builtin_pick() {  # $1 = the inputs whose transport is `builtin` (devices.json; "" when it is
  # stale), $2 = every input name, one per line -> the built-in mic, or "" (a Mac with none)
  if [ -n "$BUILTIN_MIC" ]; then echo "$BUILTIN_MIC"; return; fi
  # Of the builtin-transport inputs, the one the name pattern names; the first (sorted) only when
  # none fits (a localized name). A headset on a MacBook's jack, "External Microphone", sorts
  # first and is most likely `builtin` too (the Mac's own codec; UNMEASURED on real hardware).
  # The lid does not shut it, so taking it for the built-in held a working mic and let the real
  # built-in fail over.
  local m; m="$(awk -v p="$BUILTIN_PAT" 'f == 0 && $0 ~ p { print; f = 1 }' <<<"${1:-$2}")"
  [ -n "$m" ] || m="${1%%$'\n'*}"
  echo "$m"
}
pick_input() {  # $1 = ears.json input, $2 = macOS default input, $3 = the built-in mic ("" if
  # none is known) -> the device for the ears
  if [ "$1" != default ]; then echo "$1"; return; fi   # a pin wins, virtual or not
  if is_virtual "$2"; then echo "${3:-$BUILTIN_MIC}"; else echo "$2"; fi
}
pick_output() {  # $1 = ears.json input, $2 = macOS default output, $3 = every output
  # name, one per line -> the output to switch the Mac to, or nothing: leave it alone.
  [ "$1" = default ] || return 0                       # pinned: a call is wired up
  [ -n "$2" ] && [ "$2" != "$BUILTIN_OUT" ] || return 0
  if is_virtual "$2"; then echo "$BUILTIN_OUT"; return 0; fi
  # A multi-output is named "A + B" (bin/multi-output.swift, and "Speakers + AirPods").
  # One with AirPods in it has lost them when no plain AirPods output is left.
  case "$2" in *" + "*) ;; *) return 0 ;; esac
  grep -qi airpods <<<"$2" || return 0
  # One awk, not `grep | grep -q`: under the keeper's pipefail, grep -q exiting on its
  # first match SIGPIPEs the writer and the pipeline reads as "no AirPods".
  awk 'index($0, " + ") == 0 && tolower($0) ~ /airpods/ { f = 1 } END { exit !f }' <<<"$3" \
    || echo "$BUILTIN_OUT"
}
# --- end decisions ---
target() {  # the device the ears should be on right now
  if [ "$FOLLOW" = 1 ]; then
    local want; want="$(conf | cut -d' ' -f2-)"
    if [ "$want" = default ]; then pick_input default "$(default_input)" "$builtin_mic"; else echo "$want"; fi
  else echo "$DEVICE"; fi
}
# The output check reads the default output and the output list from input-watch's JSON
# file (docs/audio-devices.md) while it is fresh: no CoreAudio client at all. Stale or
# missing, it falls back to two SwitchAudioSource runs (a CoreAudio client each, see
# input-watch.swift); a SET is always SwitchAudioSource. It runs at start, STABLE loops (at
# least 1) after an input change (the AirPods leave both lists at once), and every OUT_EVERY
# loops as a backstop.
OUT_EVERY="${EARS_OUT_EVERY:-30}"          # 0: no backstop, only after an input change
OUT_FALLBACK="${EARS_OUTPUT_FALLBACK:-1}"   # 0: never touch the Mac's default output
# Here, between OUT_EVERY= and listen_on(): tests/test_keeper_fallback.py runs this region
# alone under set -u, so an assignment outside it would make every test fall back unseen.
AUDIO_DEVICES="${AUDIO_DEVICES_FILE:-${XDG_STATE_HOME:-$HOME/.local/state}/audio/devices.json}"
out_tried=""; out_src="file"
audio_outputs() {  # line 1 = the default output ("" if none), then one output name per
  # line, from $AUDIO_DEVICES while it is fresh. Exit 1 = stale or missing: do your own read.
  # Fresh (docs/audio-devices.md): a JSON object, version 1, and its writer's pid alive.
  python3 -c 'import json,os,sys
def bad(): sys.exit(1)
try: d=json.load(open(sys.argv[1]))
except Exception: bad()
if not isinstance(d,dict): bad()
v,p=d.get("version"),d.get("pid")
if type(v) is not int or v!=1 or type(p) is not int or p<1: bad()
try: os.kill(p,0)
except PermissionError: pass
except Exception: bad()
def name(x):
  if x is None: return ""
  if not isinstance(x,dict) or not isinstance(x.get("name"),str) or "\n" in x["name"]: bad()
  return x["name"]
o=d.get("outputs")
if not isinstance(o,list) or any(x is None for x in o): bad()
print("\n".join([name(d.get("default_output"))]+[name(x) for x in o]))' "$AUDIO_DEVICES" 2>/dev/null
}
audio_builtin_inputs() {  # the inputs whose transport is `builtin`, one name per line, from
  # $AUDIO_DEVICES while it is fresh (the same rule as audio_outputs). Exit 1 = stale or missing.
  python3 -c 'import json,os,sys
def bad(): sys.exit(1)
try: d=json.load(open(sys.argv[1]))
except Exception: bad()
if not isinstance(d,dict): bad()
v,p=d.get("version"),d.get("pid")
if type(v) is not int or v!=1 or type(p) is not int or p<1: bad()
try: os.kill(p,0)
except PermissionError: pass
except Exception: bad()
i=d.get("inputs")
if not isinstance(i,list): bad()
for x in i:
  if isinstance(x,dict) and x.get("transport")=="builtin" and isinstance(x.get("name"),str) and x["name"] and "\n" not in x["name"]: print(x["name"])' "$AUDIO_DEVICES" 2>/dev/null
}
check_output() {  # FOLLOW only: move the Mac's default output off a cable or a dead multi-output
  [ "$FOLLOW" = 1 ] && [ "$OUT_FALLBACK" = 1 ] || return 0
  local want cur all to st src   # st is local: the main loop's st is stt_conf's
  want="$(conf | cut -d' ' -f2-)"
  if st="$(audio_outputs)"; then
    src=file; cur="$(head -1 <<<"$st")"; all="$(tail -n +2 <<<"$st")"
  else
    src=SwitchAudioSource
    cur="$(SwitchAudioSource -c -t output 2>/dev/null)"
    all="$(SwitchAudioSource -a -t output 2>/dev/null)"
  fi
  if [ "$src" != "$out_src" ]; then   # one line per flip, not one per check
    if [ "$src" = file ]; then note "output check: reading $AUDIO_DEVICES again"
    else note "output check: $AUDIO_DEVICES missing or stale; asking SwitchAudioSource"; fi
    out_src="$src"
  fi
  to="$(pick_output "$want" "$cur" "$all")"
  if [ -z "$to" ]; then out_tried=""; return 0; fi
  [ "$cur" = "$out_tried" ] && return 0                  # tried this one already: no flapping
  out_tried="$cur"
  if ! grep -qxF -- "$to" <<<"$all"; then note "output '$cur' is a cable or dead; no '$to' to fall back to"; return 0; fi
  note "ears output: '$cur' -> '$to'"
  if SwitchAudioSource -t output -s "$to" >/dev/null 2>&1; then
    mail_me "ears: speaker -> $to at $(date +%H:%M) EOM"
  else note "output switch to '$to' FAILED"; fi
}
stt_conf() {  # "URL MODEL PARTIALS" from ears.json's final (old name stt) and partials, blanks when absent
  python3 -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
d=d if isinstance(d,dict) else {}
s=d.get("final") or d.get("stt") or {}
s=s if isinstance(s,dict) else {}
p=d.get("partials")
print(str(s.get("url") or "").strip(), str(s.get("model") or "").strip(), p if p in ("final","nemotron") else "final")' "$CONF"
}
listen_on() {  # 1 unless ears.json says listen: false, in FOLLOW and pinned-DEVICE mode alike
  conf | cut -d' ' -f1
}
note() { echo "$(date +%H:%M:%S) $*"; }
mail_me() {  # $1 = subject; nobody to mail (EARS_NOTIFY empty, or unset and no operator): the log line is the record
  if [ -z "$NOTIFY" ]; then note "not mailed (EARS_NOTIFY empty, or unset and no operator configured): $1"; return 0; fi
  # sessionmail's `message`: PATH, then where uv installs it, as ears.message_bin
  # (tests/test_message_bin.py holds the two). A launchd PATH holds neither.
  local m k
  m="$(command -v message 2>/dev/null)"
  for k in "$HOME/.local/bin/message" "$HOME/.local/share/uv/tools/sessionmail/bin/message"; do
    if [ -z "$m" ] && [ -f "$k" ] && [ -x "$k" ]; then m="$k"; fi
  done
  if [ -z "$m" ]; then
    note "mail FAILED (no message binary on PATH, ~/.local/bin or ~/.local/share/uv/tools/sessionmail/bin): $1"
    return 0
  fi
  "$m" send "$NOTIFY" -s "$1" --no-body >/dev/null || note "mail FAILED: $1"
}
CAPTURE_PAT="${EARS_CAPTURE_PAT:-(ears|voice_mode)\.listen capture}"   # EARS_CAPTURE_PAT: tests only
capture_pid() {  # the ROOM's capture: one a keeper started, known by the stop file only a
  # keeper mints (start_capture: $RUN/STOP-capture-keeper-HHMMSS). Every other capture is
  # someone else's and is left alone:
  # - a call leg's (`--source call:N`, from call-ears): once the AirPods
  #   left during a call, the old `pgrep | head -1` took the call's capture for the
  #   room's, and the room was deaf until the call ended (keeper log);
  # - call-tap's capture on "BlackHole 2ch": FOLLOW mode saw a capture
  #   off the default input, took it for the room's, and stopped it within a second.
  # A room capture started by hand, without a keeper's stop file, is not adopted either;
  # the startup note names it (foreign_captures) so a duplicate is visible.
  local p args
  for p in $(pgrep -f "$CAPTURE_PAT"); do
    args="$(ps -ww -o args= -p "$p" 2>/dev/null)" || continue
    case "$args" in *"--source call"*) continue ;; esac
    case "$args" in *"/STOP-capture-keeper-"*) echo "$p"; return 0 ;; esac
  done
}
foreign_captures() {  # captures that are not the room's, one "pid: argv" line each
  local p room; room="$(capture_pid)"
  for p in $(pgrep -f "$CAPTURE_PAT"); do
    [ "$p" = "$room" ] && continue
    echo "$p: $(ps -ww -o args= -p "$p" 2>/dev/null | cut -c1-160)"
  done
}
capture_running() { [ -n "$(capture_pid)" ]; }
capture_arg() {  # $1 = --device | --stop-file, read from the running capture's argv.
  # Splits argv on spaces, so "MacBook Pro Microphone" reads back as "MacBook":
  # enough for the substring test below, which is all it is used for.
  local p; p="$(capture_pid)"; [ -n "$p" ] || return 0
  ps -ww -o args= -p "$p" | tr ' ' '\n' | grep -A1 -x -- "$1" | tail -1
}
room_on_call() {  # 1 when ears.json says room_on_call: true - the room mic stays on during a call
  python3 -c 'import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
print(1 if isinstance(d,dict) and d.get("room_on_call") is True else 0)' "${CONF:-${EARS_CONF:-${VOICE_SETTINGS:-}/ears.json}}"
}
call_live() {  # the live calls the ears hear, "call:N[,call:M]"; "" when none, or room_on_call
  # SECURITY: when the user leaves the computer, somebody else at the computer must not
  # be able to do prompt injection. While the user is on a call,
  # the call is the only way in: the room capture stops, so nothing said at the Mac reaches
  # the heard log, and so no wake mail, no ride-along, no sound - every path at once, which
  # filtering each reader could not promise. The speaker label is no guard: the user's own call line
  # came back `unknown` (measured). A call is live while call-ears' probe says so
  # (calls_published), OR while a call capture (`--source call:N`) runs. The capture alone is
  # not enough: on one call the ear leg sent no audio, each capture
  # died after 10 s, and in every 2 s gap before the retry the room mic came back on.
  [ "$(room_on_call)" = 1 ] && return 0
  local p args out=""
  for p in $(pgrep -f "$CAPTURE_PAT"); do
    args="$(ps -ww -o args= -p "$p" 2>/dev/null)" || continue
    case "$args" in
      *"--source call:"*) out="$out${out:+,}$(sed -E 's/.*--source (call:[0-9]+).*/\1/' <<<"$args")" ;;
    esac
  done
  # one sorted list, so the same calls never read as a change
  { tr ',' '\n' <<<"$out"; tr ',' '\n' <<<"$(calls_published)"; } | grep . | sort -u | paste -sd, -
}
calls_published() {  # call-ears' probe, "call:N[,call:M]" from $CALL_EARS_DIR/live; "" when the
  # file is absent or older than EARS_CALLS_LIVE_AGE seconds (call-ears is down: captures decide)
  local f="${CALL_EARS_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/ears/call-ears}/live" m
  # macOS stat by path: Homebrew's GNU stat can come first on PATH, and reads -f as --file-system
  m="$(/usr/bin/stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null)" || return 0
  [ $(( $(date +%s) - m )) -le "${EARS_CALLS_LIVE_AGE:-5}" ] || return 0
  tr -d ' \n' <"$f"
}
# --- calls: the call ears, this keeper's child (tests/test_keeper_calls.py runs these alone) ---
CALLS="${EARS_CALLS:-1}"
CALL_EARS="${EARS_CALL_EARS:-$(cd "$(dirname "$0")" && pwd)/call-ears}"
# call-ears' own dir (STOP, live, its log): user state, as bin/call-tap's RUN is
# (a host keeping its old dir sets CALL_EARS_DIR to it). keeper/call-ears has the same default.
CALL_EARS_DIR="${CALL_EARS_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/ears/call-ears}"; export CALL_EARS_DIR
# A call-ears is a python running a file named call-ears: the old launch agent's, ~/.<agent>/tools'
# or ours. Not a capture's `--session call-ears-N`, not `less keeper/call-ears`.
CALL_EARS_PAT="${EARS_CALL_EARS_PAT:-[Pp]ython[0-9.]* [^ ]*/call-ears( |$)}"
calls_pid=""; calls_wait=2; calls_next=0; calls_since=0; calls_other=""
other_call_ears() {  # call-ears this keeper did not start, one "pid: argv" line each
  local p
  for p in $(pgrep -f "$CALL_EARS_PAT"); do
    [ "$p" = "$calls_pid" ] && continue
    echo "$p: $(ps -ww -o args= -p "$p" 2>/dev/null | cut -c1-160)"
  done
}
calls_next_wait() {  # $1 = seconds the last run lasted, $2 = the last wait -> the next wait
  if [ "$1" -ge 60 ]; then echo 2; else local w=$(( $2 * 2 )); echo $(( w > 300 ? 300 : w )); fi
}
calls_up() {  # keep one call-ears running, ours; call from the loop itself, never inside $(...)
  [ "$CALLS" = 1 ] || return 0
  if [ -n "$calls_pid" ] && kill -0 "$calls_pid" 2>/dev/null; then return 0; fi
  local now; now="$(date +%s)"
  if [ -n "$calls_pid" ]; then  # it ended
    wait "$calls_pid" 2>/dev/null; local rc=$?
    calls_wait="$(calls_next_wait $(( now - calls_since )) "$calls_wait")"
    calls_next=$(( now + calls_wait ))
    note "call ears: pid $calls_pid ended (exit $rc); again in ${calls_wait}s"
    calls_pid=""; rm -f "$CALLS_PIDF"
  fi
  [ "$now" -ge "$calls_next" ] || return 0
  [ -e "$CALL_EARS_DIR/STOP" ] && return 0      # stopped by hand: rm STOP and they start again
  local other; other="$(other_call_ears)"
  if [ -n "$other" ]; then
    [ "$other" = "$calls_other" ] || note "call ears: another job runs them, so not starting ours: $other"
    calls_other="$other"; calls_next=$(( now + 10 )); return 0
  fi
  [ -n "$calls_other" ] && note "call ears: the other job's are gone; starting ours"
  calls_other=""
  "$PY" "$CALL_EARS" >>"$LOGDIR/ears-keeper-calls.log" 2>&1 </dev/null &
  calls_pid=$!; calls_since="$now"; echo "$calls_pid" >"$CALLS_PIDF"
  note "call ears: up, pid $calls_pid (log $LOGDIR/ears-keeper-calls.log)"
}
calls_down() {  # stop our call-ears; the call captures it started run on to their hang-up
  [ -n "$calls_pid" ] && kill "$calls_pid" 2>/dev/null
  rm -f "$CALLS_PIDF"; calls_pid=""
}
# --- end calls ---
# The missed-reply clip: when a mouth line ends and
# no turn comes, keep that window of the user's OWN mic audio (0600). Only while
# recording is on, kept for record_days. The
# capture's clip is OPT-IN, off for every source, so this room capture, the one on the user's
# mic, turns it on. EARS_MISSED_REPLY_S: the window in seconds (default 30; 0 = off).
missed_reply_window() {  # seconds; a value that is not a plain number is 0 (off), never a dead capture
  local s="${EARS_MISSED_REPLY_S:-30}"
  if [[ "$s" =~ ^[0-9]+([.][0-9]+)?$ ]]; then echo "$s"; else echo 0; fi
}
# --- deaf: the ONE deaf/recover report path (tests/test_keeper_deaf.py runs this alone) ---
# A capture can look alive and hear nothing. Every cause of that reports through deaf_set and
# deaf_clear, and nothing else in the keeper mails about deafness. Today the cause is a missing
# Silero model (model_ready), a stopped heartbeat (beat), failing speech to
# text (stt) and a mic that gives only zeros (zeros); a lid-shut built-in mic and a deaf speakers
# sidecar join them as their trackers land.
# Per CHANNEL (`room`: the keeper's room capture; `speakers`: the sidecar, which can be deaf
# while the room hears) an episode opens at its first cause and closes when its last cause
# clears. ONE mail and one `ears-deaf` heard event when it opens; ONE mail and one
# `ears-hearing` event, naming the cause and how long it lasted, when it closes. A cause
# already open says nothing again; a new cause during an episode is a log note and an
# `ears-deaf` event, never a second mail. While the room is DEAF, start_capture's restart
# mails are only notes (mail_unless_deaf): the DEAF mail is the one mail. A channel or cause is
# one lower-case word. When several causes hold, the first in DEAF_ORDER names the episode.
# Subjects stay within 64 characters and end EOM; a device is cut to 20, as lag does.
DEAF_ORDER="model lid stt beat zeros"   # precedence; a cause not listed ranks after them all
DEAF_BACKOFF_MIN="${EARS_BACKOFF_MIN:-30}"; DEAF_BACKOFF_MAX="${EARS_BACKOFF_MAX:-300}"
deaf_now() { echo "${EARS_NOW:-$(date +%s)}"; }   # EARS_NOW: tests only, a fake clock
deaf_word() { [[ "$1" =~ ^[a-z][a-z0-9_]*$ ]]; }   # safe inside a variable name
deaf_get() { local v="deaf_$1"; echo "${!v:-}"; }  # $1 = causes_CH | name_CH | since_CH | dev_CH
deaf_put() { printf -v "deaf_$1" '%s' "$2"; }
deaf_rank() {  # $1 = cause -> its place in DEAF_ORDER (1 first), 99 when not listed
  local c i=1; for c in $DEAF_ORDER; do [ "$c" = "$1" ] && { echo "$i"; return; }; i=$((i + 1)); done; echo 99
}
deaf_name() {  # $1 = causes, space-separated -> the one that names the episode
  local c best="" r br=100
  for c in $1; do r="$(deaf_rank "$c")"; if [ "$r" -lt "$br" ]; then best="$c"; br="$r"; fi; done
  echo "$best"
}
deaf_on() { [ -n "$(deaf_get "causes_$1")" ]; }   # $1 = channel: 0 while it is DEAF
deaf_room() { deaf_on room; }
deaf_cause() { deaf_name "$(deaf_get "causes_$1")"; }   # $1 = channel -> the cause naming it, or ""
deaf_dur() {  # $1 = seconds -> "45s", "12m", "4h40m"
  local s="${1:-0}"; [ "$s" -ge 0 ] 2>/dev/null || s=0
  if [ "$s" -lt 90 ]; then echo "${s}s"; elif [ "$s" -lt 3600 ]; then echo "$((s / 60))m"
  else echo "$((s / 3600))h$((s % 3600 / 60))m"; fi
}
deaf_subject() {  # $1 channel, $2 cause, $3 detail, $4 device -> the DEAF mail's subject, <= 64
  local who="ears:" dev="${4:0:20}" head tail body max
  [ "$1" = room ] || who="ears: ${1:0:10}"
  # A cause with no line of its own yet: its name and detail, cut to fit.
  head="$who DEAF, "; tail="${dev:+ on $dev} at $(date +%H:%M) EOM"; body="$2${3:+: $3}"
  case "$2" in
    model) echo "$who DEAF, no Silero model: run ears install EOM"; return ;;
    stt)   # the room's: "ears: DEAF, STT failing (500) at HH:MM EOM" (detail: the HTTP code, or "?")
      if [ "$1" = room ]; then echo "$who DEAF, STT failing (${3:0:5}) at $(date +%H:%M) EOM"; return; fi ;;
    beat)  # the room's: "ears: DEAF, no heartbeat since HH:MM on DEVICE EOM" (detail "since HH:MM")
      if [ "$1" = room ]; then head="$head""no heartbeat "; tail="${dev:+ on $dev} EOM"; body="${3:-since ?}"; fi ;;
    lid)   # the room's. Not "no external mic": a jack headset can be there (it holds, it does not pick)
      if [ "$1" = room ]; then echo "$who DEAF, lid shut, built-in mic off, holding at $(date +%H:%M) EOM"; return; fi ;;
    zeros) # the room's: "ears: DEAF, mic gives only zeros on DEVICE at HH:MM EOM". The design's
      # words with a 20-character device come to 69; the device is cut to 15 to keep 64.
      if [ "$1" = room ]; then echo "$who DEAF, mic gives only zeros${4:+ on ${4:0:15}} at $(date +%H:%M) EOM"; return; fi ;;
    cover) # the side-car's: "ears: speakers DEAF, 0% of N lines on DEVICE EOM" (detail N, <= 3 digits)
      if [ "$1" = speakers ]; then echo "$who DEAF, 0% of ${3:0:3} lines${dev:+ on $dev} EOM"; return; fi ;;
  esac
  max=$(( 64 - ${#head} - ${#tail} )); [ "$max" -ge 1 ] || max=1
  body="${body:0:$max}"; body="${body%% }"; body="${body%:}"   # no dangling ": " when cut
  echo "$head$body$tail"
}
deaf_recovered_subject() {  # $1 channel, $2 device -> the recovery mail's subject, <= 64
  if [ "$1" = room ]; then echo "ears: hearing again${2:+ on ${2:0:20}} at $(date +%H:%M) EOM"
  else echo "ears: ${1:0:10} hearing again at $(date +%H:%M) EOM"; fi
}
deaf_event() {  # $1 event, $2 device, $3 text -> one heard-log line
  $HEARD write event "$1" --device "${2:-none}" --text "$3" >/dev/null || note "heard write FAILED"
}
deaf_set() {  # $1 channel, $2 cause, $3 detail ("" for none), $4 device ("" if unknown)
  local ch="$1" cause="$2" detail="${3:-}" dev="${4:-}" causes was
  if ! deaf_word "$ch" || ! deaf_word "$cause"; then note "deaf_set: bad channel or cause '$ch' '$cause'"; return 1; fi
  causes="$(deaf_get "causes_$ch")"
  case " $causes " in *" $cause "*) return 0 ;; esac   # DEAF for this already: nothing new
  [ -n "$dev" ] && deaf_put "dev_$ch" "$dev"
  dev="$(deaf_get "dev_$ch")"
  if [ -z "$causes" ]; then   # the episode opens: the one mail
    deaf_put "causes_$ch" "$cause"; deaf_put "name_$ch" "$cause"; deaf_put "since_$ch" "$(deaf_now)"
    note "DEAF ($ch): $cause${detail:+: $detail}"
    deaf_event ears-deaf "$dev" "$ch: $cause${detail:+: $detail}"
    mail_me "$(deaf_subject "$ch" "$cause" "$detail" "$dev")"
    return 0
  fi
  was="$(deaf_name "$causes")"; deaf_put "causes_$ch" "$causes $cause"
  # the episode keeps its most specific cause for the recovery line, even after that one clears
  deaf_put "name_$ch" "$(deaf_name "$(deaf_get "name_$ch") $cause")"
  note "DEAF ($ch): also $cause${detail:+: $detail}; named $(deaf_cause "$ch") (was $was); not mailed, one was sent"
  deaf_event ears-deaf "$dev" "$ch: $cause${detail:+: $detail}"
}
deaf_clear() {  # $1 channel, $2 cause that no longer holds, $3 device now in use ("" if unknown)
  local ch="$1" cause="$2" dev="${3:-}" causes rest="" c name since dur
  if ! deaf_word "$ch" || ! deaf_word "$cause"; then note "deaf_clear: bad channel or cause '$ch' '$cause'"; return 1; fi
  causes="$(deaf_get "causes_$ch")"
  case " $causes " in *" $cause "*) ;; *) return 0 ;; esac   # not DEAF for this: nothing to say
  for c in $causes; do [ "$c" = "$cause" ] || rest="$rest${rest:+ }$c"; done
  deaf_put "causes_$ch" "$rest"
  if [ -n "$rest" ]; then note "($ch) $cause cleared; still DEAF: $(deaf_name "$rest")"; return 0; fi
  name="$(deaf_get "name_$ch")"; since="$(deaf_get "since_$ch")"
  dur="$(deaf_dur $(( $(deaf_now) - ${since:-$(deaf_now)} )))"
  [ -n "$dev" ] || dev="$(deaf_get "dev_$ch")"
  note "hearing again ($ch)${dev:+ on $dev}: was $name for $dur"
  deaf_event ears-hearing "$dev" "$ch: hearing again, was $name for $dur"
  mail_me "$(deaf_recovered_subject "$ch" "$dev")"
  deaf_put "name_$ch" ""; deaf_put "since_$ch" ""; deaf_put "dev_$ch" ""
}
mail_unless_deaf() {  # $1 = subject: a restart mail is only a note while the room is DEAF
  if deaf_room; then note "not mailed, the room is DEAF ($(deaf_cause room)): $1"; else mail_me "$1"; fi
}
restart_backoff() {  # $1 = 1 when the last capture heard, else 0; $2 = the last wait in s (0: none)
  # -> seconds before the next restart: 0 after a capture that heard, else 30 doubling to 300
  # (EARS_BACKOFF_MIN, EARS_BACKOFF_MAX). Pure: the stt region's restart gate waits on it.
  local w="${2:-0}"; [[ "$w" =~ ^[0-9]+$ ]] || w=0
  if [ "${1:-0}" = 1 ]; then echo 0; return; fi
  w=$(( w * 2 ))
  [ "$w" -ge "$DEAF_BACKOFF_MIN" ] || w="$DEAF_BACKOFF_MIN"
  [ "$w" -le "$DEAF_BACKOFF_MAX" ] || w="$DEAF_BACKOFF_MAX"
  echo "$w"
}
# --- end deaf ---
# --- model: the Silero gate (tests/test_keeper_model.py runs this alone) ---
# With no model the capture died "Silero model not found" (src/ears/listen/
# silero.py) and the keeper started it again, every 4 s, forever (seen on a new host's install).
# Now no capture starts without the model: the keeper says so ONCE, through the
# deaf path (deaf_set room model: one log line, one heard event, one mail when there is someone
# to mail) and HOLDS, looking again every EARS_MODEL_RECHECK seconds (default 30). It never
# exits: the agent is KeepAlive, so an exit is the same loop. The model arriving lets the
# capture start, and the episode ends when that capture is seen running (model_started:
# deaf_clear, the ONE recovery mail). Until then the room is still DEAF, so the start's own
# `capture restarted` is only a note: one mail when the model arrives, not two.
MODEL_RECHECK="${EARS_MODEL_RECHECK:-30}"
model_missing=0; model_next=0
model_ready() {  # $1 = the device it would start on; 0 when $SILERO is there; else 1, said
  # once (deaf_set), re-checked slowly
  local now; now="$(date +%s)"
  if [ "$model_missing" = 1 ]; then
    [ "$now" -ge "$model_next" ] || return 1
    model_next=$(( now + MODEL_RECHECK ))
  fi
  if [ -s "$SILERO" ]; then
    if [ "$model_missing" = 1 ]; then
      model_missing=0
      note "Silero model found at $SILERO: the capture may start"
    fi
    return 0
  fi
  if [ "$model_missing" = 0 ]; then
    model_missing=1; model_next=$(( now + MODEL_RECHECK ))
    note "Silero model not found: $SILERO. No capture until it is there (run \`ears install\`); looking again every ${MODEL_RECHECK}s, quietly"
    deaf_set room model "$SILERO" "${1:-}"
  fi
  return 1
}
model_started() {  # $1 = the device: a capture is running, so a model DEAF episode is over
  deaf_clear room model "${1:-}"
}
# --- end model ---
# --- heard: the ONE reader of the heard log (tests/test_keeper_lag.py and test_keeper_beat.py run it) ---
# The captures have no mailer; the keeper is the ears' mail path (mail_me), so it reads the heard
# log's NEW lines once per loop, and only when the file grew, and hands each line it cares about
# to its watcher: a recogniser-lag alarm or ok to the lag region, a mic heartbeat to the beat
# region. One embedded python per loop at most, as before. A line still being written waits for
# its newline; a new day's log is read from its top; a log cut short is read again. A capture's
# listen-stopped and its partials and turns go to the stt region. The speaker side-car's
# rows, speakers_DAY.jsonl beside the heard log, are read in the SAME python, with their own offset
# and the same day-roll and cut-short rules, and go to the speakers region.
# --- sessionvoice_home: a copy of ears.home.sessionvoice_home (tests/test_home.py holds it) ---
# $SESSIONVOICE_HOME, $VOICEMODE_BASE_DIR, ~/.sessionvoice or ~/.voicemode if it holds the
# heard log (a heard.lock or a heard_*.jsonl in logs/conversations), else ~/.sessionvoice.
sessionvoice_home() {
  local v d
  for v in "${SESSIONVOICE_HOME:-}" "${VOICEMODE_BASE_DIR:-}"; do
    [ -n "$v" ] || continue
    case $v in "~" | "~/"*) v="$HOME${v#\~}" ;; esac
    echo "$v"; return 0
  done
  for d in "$HOME/.sessionvoice" "$HOME/.voicemode"; do
    if [ -e "$d/logs/conversations/heard.lock" ] \
        || compgen -G "$d/logs/conversations/heard_*.jsonl" >/dev/null; then
      echo "$d"; return 0
    fi
  done
  echo "$HOME/.sessionvoice"
}
# --- end sessionvoice_home ---
HEARD_DIR="${EARS_HEARD_DIR:-$(sessionvoice_home)/logs/conversations}"
heard_file=""; heard_off=0; spk_file=""; spk_off=0
heard_events() {  # $1 = heard log, $2 = byte offset -> line 1 the offset after the last whole line,
  # then one TAB line per event in between:
  #   alarm LAG DEVICE | ok LASTED DEVICE      a recogniser-lag / recogniser-lag-ok event
  #   beat PID SESSION SOURCE PEAK DEVICE      a heartbeat; PID "-" when the row has none (a
  #                                            capture from before beats carried one), PEAK "-" too
  #   stopped PID SESSION SOURCE REASON CODE DEVICE
  #                                            a listen-stopped. REASON is the row's, but `stt`
  #                                            for an error that carries "STT failed"; CODE is
  #                                            the HTTP status in the error (its URLs cut out
  #                                            first, so a port is never read), else "-"
  #   heard PID SESSION SOURCE DEVICE          a partial or turn with text: speech to text works.
  #                                            A run of the same line is printed once
  # With $3 = the side-car's speakers log and $4 = its byte offset, it reads that too, after them:
  #   spk COVERED DEVICE                       a mic row (no `leg`: a call's rows are not the
  #                                            room's). COVERED 0 = 0% of the line covered:
  #                                            `cover` == 0, or, before the side-car writes
  #                                            `cover`, a why ending "(0% of the line covered)".
  #                                            1 = some: a row with no why was heard, and a
  #                                            "no signal: the device gave only zeros" row says
  #                                            "(N% ...)" with N > 0. A row that says neither
  #                                            (a window in the future) is not printed
  #   spkoff OFFSET                            last: the offset after its last whole line
  # Every field is non-empty ("?" for none): the TAB read would close up an empty one.
  python3 -c 'import json,re,sys
p,off=sys.argv[1],int(sys.argv[2])
try:
  with open(p,"rb") as f:
    f.seek(off); b=f.read()
except OSError: b=b""
end=b.rfind(b"\n")+1
print(off+end)
def txt(v,n=None):
  v=str(v if v is not None else "?").replace("\t"," ").replace("\n"," ") or "?"
  return v[:n] if n else v
last=None
def out(l,once=False):
  global last
  if not (once and l==last): print(l)
  last=l
WANT=(b"recogniser-lag",b"heartbeat",b"listen-stopped",b"\"partial\"",b"\"turn\"")
for raw in b[:end].splitlines():
  if not any(w in raw for w in WANT): continue
  try: d=json.loads(raw)
  except ValueError: continue
  if not isinstance(d,dict): continue
  k,e=d.get("kind"),d.get("event"); dev=txt(d.get("device") or d.get("source"),20)
  pid=d.get("pid"); pid=str(pid) if type(pid) is int and pid>0 else "-"
  who="%s\t%s\t%s" % (pid,txt(d.get("session")),txt(d.get("source")))
  if k in ("partial","turn"):
    t=d.get("text")
    if isinstance(t,str) and t.strip(): out("heard\t%s\t%s" % (who,dev),once=True)
    continue
  if k!="event": continue
  num=lambda f: d.get(f) if isinstance(d.get(f),(int,float)) and not isinstance(d.get(f),bool) else None
  if e=="recogniser-lag" and num("lag_s") is not None: out("alarm\t%d\t%s" % (round(num("lag_s")),dev))
  elif e=="recogniser-lag-ok": out("ok\t%s\t%s" % ("?" if num("lasted_s") is None else round(num("lasted_s")),dev))
  elif e=="heartbeat":
    peak="-" if num("peak") is None else str(round(num("peak")))
    out("beat\t%s\t%s\t%s" % (who,peak,dev))
  elif e=="listen-stopped":
    err=str(d.get("error") or ""); r=txt(d.get("reason"))
    if r=="error" and "STT failed" in err: r="stt"
    m=re.search(r"\b[45]\d\d\b",re.sub(r"\w+://\S+","",err))   # a port in a URL (:443) is no code
    out("stopped\t%s\t%s\t%s\t%s" % (who,r,m.group(0) if m else "-",dev))
if len(sys.argv)>4:
  sp,so=sys.argv[3],int(sys.argv[4])
  try:
    with open(sp,"rb") as f:
      f.seek(so); sb=f.read()
  except OSError: sb=b""
  se=sb.rfind(b"\n")+1
  for raw in sb[:se].splitlines():
    try: d=json.loads(raw)
    except ValueError: continue
    if not isinstance(d,dict) or d.get("leg"): continue
    c,why=d.get("cover"),d.get("why")
    if isinstance(c,(int,float)) and not isinstance(c,bool): z=0 if c<=0 else 1
    elif why is None: z=1
    elif not isinstance(why,str): continue
    elif why.endswith("(0% of the line covered)"): z=0
    elif re.search(r"\(\d+% of the line covered\)$",why): z=1
    else: continue
    print("spk\t%d\t%s" % (z,txt(d.get("device"),20)))
  print("spkoff\t%d" % (so+se))' "$@"
}
heard_start() {  # at keeper start: mark the end of today's log, so lines from before us are not news
  heard_file="$HEARD_DIR/heard_$(date +%Y-%m-%d).jsonl"
  heard_off="$(wc -c <"$heard_file" 2>/dev/null | tr -d ' ')"; heard_off="${heard_off:-0}"
  spk_file="$HEARD_DIR/speakers_$(date +%Y-%m-%d).jsonl"
  spk_off="$(wc -c <"$spk_file" 2>/dev/null | tr -d ' ')"; spk_off="${spk_off:-0}"
}
heard_watch() {  # read the lines the captures wrote since the last look; call it every loop
  local f s size ssize day out kind a b c d e g
  day="$(date +%Y-%m-%d)"; f="$HEARD_DIR/heard_$day.jsonl"; s="$HEARD_DIR/speakers_$day.jsonl"
  if [ "$f" != "$heard_file" ]; then heard_file="$f"; heard_off=0; fi       # a new day's log
  if [ "$s" != "$spk_file" ]; then spk_file="$s"; spk_off=0; fi
  size=0; [ -f "$f" ] && size="$(wc -c <"$f" | tr -d ' ')"
  ssize=0; [ -f "$s" ] && ssize="$(wc -c <"$s" | tr -d ' ')"
  [ "$size" -lt "$heard_off" ] && heard_off=0                                # cut short: read it again
  [ "$ssize" -lt "$spk_off" ] && spk_off=0
  [ "$size" -gt "$heard_off" ] || [ "$ssize" -gt "$spk_off" ] || return 0  # neither grew: no python
  out="$(heard_events "$f" "$heard_off" "$s" "$spk_off")" || return 0
  heard_off="$(head -1 <<<"$out")"
  while IFS=$'\t' read -r kind a b c d e g; do
    case "$kind" in
      alarm) lag_alarm "$a" "$b" ;;
      ok) lag_ok "$a" "$b" ;;
      beat) beat_seen "$a" "$b" "$c" "$d" "$e" ;;
      stopped) stt_stopped "$a" "$b" "$c" "$d" "$e" "$g" ;;
      heard) stt_heard "$a" "$b" "$c" "$d" ;;
      spk) spk_seen "$a" "$b" ;;
      spkoff) spk_off="$a" ;;
    esac
  done < <(tail -n +2 <<<"$out")
}
# --- end heard ---
# --- lag: the recogniser-lag mail (tests/test_keeper_lag.py runs this alone) ---
# On 10-07 the room capture fell 64-108 s behind its audio and nobody knew. The capture now
# writes ONE `recogniser-lag` event when a partial or a heartbeat is over LAG_WARN_S (src/ears/
# lag.py) and one `recogniser-lag-ok` when it catches up. The heard reader (above) hands each to
# lag_alarm / lag_ok, which mail the alarm: "ears: recogniser N s behind on DEVICE at HH:MM EOM".
# Never more than one mail per EARS_LAG_MAIL_GAP seconds (default 600): a lag that flaps across
# its threshold is still one mail, and the rest are noted in the log. The device is cut to 20
# characters, so a subject stays within the 69 a mail allows. Nothing is restarted: a lagging
# recogniser is reported, not cured here.
LAG_MAIL_GAP="${EARS_LAG_MAIL_GAP:-600}"
lag_mailed_at=""
lag_alarm() {  # $1 = seconds behind, $2 = device
  local now; now="$(date +%s)"
  if [ -n "$lag_mailed_at" ] && [ $(( now - lag_mailed_at )) -lt "$LAG_MAIL_GAP" ]; then
    note "recogniser ${1} s behind on $2; not mailed, one was sent $(( now - lag_mailed_at )) s ago"
  else
    note "recogniser ${1} s behind on $2: mailing"
    mail_me "ears: recogniser ${1} s behind on $2 at $(date +%H:%M) EOM"
    lag_mailed_at="$now"
  fi
}
lag_ok() { note "recogniser caught up on $2 after ${1} s"; }   # $1 = seconds it lasted, $2 = device
# --- end lag ---
# --- stt: failing speech to text is DEAF, and room restarts back off (tests/test_keeper_stt_deaf.py runs this alone) ---
# A capture whose speech to text fails max_stt_failures times in a row (3) stops itself:
# `listen-stopped reason=error error="RuntimeError: STT failed 3 times in a row: ... 500 ..."`
# (src/ears/listen/loop.py). The keeper saw no capture and started one, which failed again as
# soon as three chunks of speech reached the broken server: a restart every ~30 s, each one a
# `capture restarted` mail or none, and never "STT failing". Now:
# - EARS_STT_DEAF (2) such stops in a row, with no heard line between them, is DEAF, cause
#   `stt`, through the deaf path: ONE mail, `ears: DEAF, STT failing (500) at HH:MM`, the code
#   from the error, `(?)` when it has none. Any other stop of the room capture breaks the run.
# - The first heard line (a partial or turn with text from the room capture: STT answered) clears
#   it: ONE recovery mail.
# - A room capture that ends by itself (any reason but `stop`, which is the keeper's own STOP
#   file or a signal) arms restart_backoff: 30 s doubling to 5 min before the next start, and
#   none after a capture that heard. While the room is DEAF the restarts are only notes
#   (mail_unless_deaf), so a broken STT server is one mail, not a mail every 30 s.
# - The wait belongs to the input it was armed on (restart_on: the input start_capture last
#   started the room capture on, room_input). It never holds a start on another input, whichever
#   the keeper reads first, the old capture's stop row or the FOLLOW move (measured: the
#   capture lost with its device can stall out after the move is seen). On a new input the
#   doubling starts again at 30 s.
# A row is the room capture's when its source is `mic` and its session is the one the keeper
# launches with (launch_capture's --session): listen-stopped, partial and turn rows carry no pid.
STT_DEAF="${EARS_STT_DEAF:-2}"
stt_fails=0; room_heard=0; restart_wait=0; restart_next=0; restart_on=""; room_input=""
room_session() { echo "${EARS_SESSION:-${CLAUDE_CODE_SESSION_ID:-ears-keeper}}"; }   # launch_capture's
stt_room() { [ "$2" = mic ] && [ "$1" = "$(room_session)" ]; }   # $1 session, $2 source: the room's?
stt_heard() {  # $1 pid, $2 session, $3 source, $4 device: a partial or turn with text
  stt_room "$2" "$3" || return 0
  room_heard=1; stt_fails=0
  deaf_clear room stt "$4"
}
stt_stopped() {  # $1 pid, $2 session, $3 source, $4 reason (`stt`: STT failed), $5 HTTP code or "-", $6 device
  stt_room "$2" "$3" || return 0
  local heard="$room_heard" code="$5"; room_heard=0
  [ "$code" = - ] && code="?"
  if [ "$4" = stt ]; then
    stt_fails=$(( stt_fails + 1 ))
    note "capture stopped: STT failing ($code), $stt_fails in a row"
    if [ "$stt_fails" -ge "$STT_DEAF" ]; then deaf_set room stt "$code" "$6"; fi
  else
    stt_fails=0
  fi
  [ "$4" = stop ] && return 0   # asked to stop (the keeper's STOP file, a signal): no wait
  [ "$room_input" = "$restart_on" ] || restart_wait=0   # another input: the doubling starts afresh
  restart_wait="$(restart_backoff "$heard" "$restart_wait")"
  restart_next=$(( $(deaf_now) + restart_wait )); restart_on="$room_input"
  if [ "$restart_wait" -gt 0 ]; then note "capture ended ($4) and heard nothing: next start in ${restart_wait}s"; fi
  return 0
}
restart_due() {  # $1 = the input it would start on (optional) -> 0 when the room capture may start now
  # A wait armed on another input never holds this one: that capture was lost with its device.
  [ -n "${1:-}" ] && [ -n "$restart_on" ] && [ "$1" != "$restart_on" ] && return 0
  [ "$(deaf_now)" -ge "$restart_next" ]
}
restart_reset() {  # $1 = the new input. The backoff is for a loop on the SAME broken thing: a new
  # FOLLOW target is a new device, so its first start waits for nothing
  if [ "$restart_wait" -gt 0 ] && ! restart_due; then note "input is now ${1:-none}: the restart wait (${restart_wait}s) is dropped"; fi
  restart_wait=0; restart_next=0
}
# --- end stt ---
# --- lid: a shut lid is a deaf built-in mic; HOLD, never fail over to it (tests/test_keeper_lid.py runs this alone) ---
# The AirPods left with the lid shut, macOS made the built-in mic the default, and FOLLOW moved
# the room capture onto it: a mic disconnected in hardware that still hands over frames of zeros.
# The keeper mailed `mic ->` and `capture restarted`, and the ears were deaf. Now:
# - The built-in is found by transport (builtin_pick, decisions region), again whenever the
#   inputs change (builtin_refresh), and the lid is probed only while it is the target, at most
#   every EARS_LID_EVERY s (5): `ioreg -r -k AppleClamshellState -d 1`, or EARS_LID_PROBE, a
#   command whose first line is the state (tests). `unknown` (a desktop) counts as open.
# - Lid shut and the target the built-in: deaf_set room lid, ONE mail, `ears: DEAF, lid shut,
#   built-in mic off, holding at HH:MM`. The keeper starts nothing, stops a room capture on the built-in
#   at once and one on the input the Mac left once the move holds STABLE loops; it HOLDS, and does not go looking for another input macOS did not make the default
#   (hold, don't pick). The stop is stop_capture as shipped.
# - lid_watch runs BEFORE the FOLLOW mail. During a lid episode a `mic ->` change is
#   a note, and so is the start that ends the hold (mail_unless_deaf): ONE mail each way.
# - It ends on the room capture's first beat with sound once the hold is over: the external
#   input returned, or the lid opened, and that capture hears. ONE recovery mail. (Not the bare
#   lid opening: that would mail `hearing again` and then `capture restarted`, two mails.)
# - A hold starts nothing, so it arms and advances no restart wait; a capture stopped for it says
#   reason `stop`, which arms none either (stt region).
LID_EVERY="${EARS_LID_EVERY:-5}"; [[ "$LID_EVERY" =~ ^[0-9]+$ ]] || LID_EVERY=5
lid_now=""; lid_next=0; lid_on=0; builtin_mic=""; builtin_for="-"
builtin_refresh() {  # $1 = every input name: find the built-in mic again; call it when they change
  local b; b="$(audio_builtin_inputs)" || b=""
  builtin_mic="$(builtin_pick "$b" "$1")"; builtin_for="$1"
}
lid_builtin() { [ -n "$builtin_mic" ] && [ -n "${1:-}" ] && grep -qxiF -- "$builtin_mic" <<<"$1"; }   # $1 = an input
lid_look() {  # sets lid_now to closed, open or unknown; probes at most every LID_EVERY s,
  # unless $1 = fresh (a once-a-capture judgement that must not trust a cached state)
  local now s; now="$(deaf_now)"
  [ "${1:-}" != fresh ] && [ -n "$lid_now" ] && [ "$now" -lt "$lid_next" ] && return 0
  if [ -n "${EARS_LID_PROBE:-}" ]; then s="$("$EARS_LID_PROBE" 2>/dev/null)"; s="${s%%$'\n'*}"
  else s="$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null \
    | awk '/"AppleClamshellState"/ { st = ($0 ~ /= *Yes/) ? "closed" : "open" } END { print st }')"; fi
  s="${s//[[:space:]]/}"
  case "$s" in closed|open) ;; *) s=unknown ;; esac   # unknown: a desktop, no clamshell; open
  [ "$s" = "$lid_now" ] || note "lid: ${lid_now:-?} -> $s"
  lid_now="$s"; lid_next=$(( now + LID_EVERY ))
}
lid_capture_builtin() {  # $1 = a capture's device AS capture_arg READS IT BACK: argv split on
  # spaces, so its FIRST word ("MacBook") -> 0 when that capture is on the built-in: the same
  # first word, whole (an exact match on the full name never held). Not a
  # substring: "Microphone USB" reads back "Microphone", which is inside "MacBook Air
  # Microphone", and its zeros were then taken for the lid's and reported by nobody
  [ -n "$builtin_mic" ] && [ -n "${1:-}" ] && grep -qxiF -- "$1" <<<"${builtin_mic%% *}"
}
lid_shut() { lid_capture_builtin "$1" && lid_look fresh && [ "$lid_now" = closed ]; }   # $1 = the capture's device (zeros' hook)
lid_watch() {  # $1 = the target. Every loop, BEFORE the FOLLOW mail. Sets lid_on: 1 = hold
  lid_on=0
  lid_builtin "$1" || return 0   # not the built-in: no probe
  lid_look
  if [ "$lid_now" = closed ]; then lid_on=1; deaf_set room lid shut "$1"; fi
  return 0
}
lid_deaf() { case " $(deaf_get causes_room) " in *" lid "*) return 0 ;; esac; return 1; }   # a lid episode?
lid_beat() {  # $1 peak ("-": none), $2 device: a room beat. With sound and no hold, a lid episode is over
  [ "$lid_on" = 0 ] && [ "$1" != - ] && [ "$1" != 0 ] && deaf_clear room lid "$2"
  return 0
}
# --- end lid ---
# --- zeros: a mic that gives only zeros is DEAF, not a quiet room (tests/test_keeper_zeros.py runs this alone) ---
# A lid-shut built-in mic, a mic with no TCC grant and a stream that has stopped delivering sound
# all hand the capture frames of zeros. Frames arrive, so it never says "no audio"; it beats every
# 15 s with `peak: 0`, and the keeper counted it as hearing. A quiet room is not that:
# its peak is low but never 0 (the peak is the largest sample in the period, an int16).
# - EARS_ZERO_BEATS (4: a minute) of the room capture's beats in a row with peak 0 is DEAF, cause
#   `zeros`, through the deaf path: ONE mail, `ears: DEAF, mic gives only zeros on DEVICE`.
# - Then ONE reopen: zeros_check stops the capture (stop_capture, a STOP file it honours at once)
#   and the loop starts a fresh one on the same input. Its stop row says reason `stop`, so the
#   restart backoff does not hold it. If the fresh one gives only zeros too, the keeper HOLDS:
#   DEAF, reported, no second reopen, no restart loop.
# - A beat with peak > 0 ends it: ONE recovery mail, and the next episode may reopen once again.
# The beat region decides which beats are the room capture's and hands each one here
# (zeros_seen). The count starts again on each new capture; the one reopen is per episode.
# zeros_lid is the lid's hook: when it says the zeros are a shut lid on the
# built-in mic, the cause is `lid`, and the lid region reports and holds; this region does nothing.
ZERO_BEATS="${EARS_ZERO_BEATS:-4}"; [[ "$ZERO_BEATS" =~ ^[1-9][0-9]*$ ]] || ZERO_BEATS=4
zeros_pid=""; zeros_n=0; zeros_dev=""; zeros_due=0; zeros_reopened=0
zeros_lid() {  # $1 = the room capture's device as capture_arg reads it -> 0 when its zeros are a shut lid
  lid_shut "$1"   # the lid region's: the built-in (by transport) with the lid shut
}
zeros_seen() {  # $1 the room capture's pid, $2 peak ("-": the row has none), $3 device: one room beat
  [ "$2" = - ] && return 0                       # no peak: says nothing either way
  if [ "$1" != "$zeros_pid" ]; then zeros_pid="$1"; zeros_n=0; fi   # a new capture counts afresh
  if [ "$2" != 0 ]; then                          # sound: hearing, whatever the room's level
    zeros_n=0; zeros_due=0; zeros_reopened=0
    deaf_clear room zeros "$3"
    return 0
  fi
  zeros_n=$(( zeros_n + 1 )); zeros_dev="$3"
  [ "$zeros_n" -eq "$ZERO_BEATS" ] || return 0   # judged once per capture, at the threshold
  local cur; cur="$(capture_arg --device)"   # the capture's own: its first word (argv split on spaces)
  if zeros_lid "${cur:-$3}"; then note "capture $1 on $3: $zeros_n beats of peak 0, and the lid is shut: the lid's"; return 0; fi
  deaf_set room zeros "$zeros_n beats of peak 0" "$3"
  if [ "$zeros_reopened" = 1 ]; then
    note "capture $1 on $3: still only zeros after the reopen: holding, DEAF and reported; no second reopen"
  else
    zeros_due=1
  fi
}
zeros_check() {  # the ONE reopen; call it every loop, after heard_watch (and beat_check)
  [ "$zeros_due" = 1 ] || return 0
  zeros_due=0; zeros_reopened=1
  if [ "$(capture_pid)" != "$zeros_pid" ]; then
    note "capture $zeros_pid gave only zeros, but it is gone already: the next start is the reopen"; return 0
  fi
  note "capture $zeros_pid on $zeros_dev: $ZERO_BEATS beats of peak 0: reopening it, once"
  stop_capture || note "capture $zeros_pid did not stop"
}
# --- end zeros ---
# --- speakers: the side-car hears nothing, 0% of its lines covered (tests/test_keeper_speakers_deaf.py runs this alone) ---
# The speaker side-car (speakers/speaker-sidecar2.py) opens the room capture's mic and names who
# spoke each heard line. Its stream can open, stay active and deliver nothing: every line then gets
# a row `why: "no audio from 'DEV' (0% of the line covered)"`. 27k such rows in 3h19m went unread
# (measured). The heard reader hands the keeper each of its mic rows (spk_seen); this region:
# - EARS_SPEAKERS_DEAF_LINES (20) mic rows in a row with 0% of the line covered is DEAF on the
#   `speakers` channel, cause `cover`, through the deaf path: ONE mail, `ears: speakers DEAF, 0% of
#   N lines on DEVICE`. The reopen is the side-car's own; the keeper only reports.
# - A mic row with any coverage ends it: ONE recovery mail, `ears: speakers hearing again`. A
#   "no signal: the device gave only zeros" row is covered (the room's zeros cause says that one).
# - MUTED while the room is DEAF or no room capture runs: the side-car hears only while the keeper's
#   capture runs, so its 0% rows are then a consequence, not a second fault. A muted row counts
#   nothing and starts the run again. "No room capture" is the beat region's beat_pid: what
#   capture_pid said at the top of this loop (beat_track), so no row costs a second ps.
SPK_DEAF_LINES="${EARS_SPEAKERS_DEAF_LINES:-20}"
[[ "$SPK_DEAF_LINES" =~ ^[1-9][0-9]{0,2}$ ]] || SPK_DEAF_LINES=20   # 1-999: N fits the subject
spk_n=0
spk_muted() { deaf_room || [ -z "${beat_pid:-}" ]; }
spk_seen() {  # $1 = 0 (a mic row with 0% of its line covered) or 1 (some covered), $2 = its device
  if [ "$1" != 0 ]; then spk_n=0; deaf_clear speakers cover "$2"; return 0; fi
  if spk_muted; then spk_n=0; return 0; fi
  spk_n=$(( spk_n + 1 ))
  [ "$spk_n" -ge "$SPK_DEAF_LINES" ] && deaf_set speakers cover "$SPK_DEAF_LINES" "$2"
  return 0
}
# --- end speakers ---
# --- beat: a capture whose heartbeat stopped is DEAF (tests/test_keeper_beat.py runs this alone) ---
# Once a room capture was alive for 18 h, but its last heartbeat was 4h40m old,
# and nothing said so: the keeper counted a live pid as healthy. Now it watches the room
# capture's heartbeats (every 15 s, `--heartbeat 15`) through the heard reader, and judges the
# capture by them, with TWO thresholds:
# - EARS_BEAT_STALE (120 s) with no beat: DEAF, cause `beat`, through the deaf path (one mail).
#   Only a report: at every end of turn the capture's frame loop waits on its speech to text
#   (src/ears/listen/loop.py _collect(wait=True), the redecode), up to ~35 s a call, so a capture
#   that is slow but alive can go well past 60 s without a beat. Its next beat is the recovery.
# - EARS_BEAT_KILL (300 s) with no beat: wedged. stop_capture stops it hard (STOP file, SIGTERM,
#   SIGKILL), and the loop starts a fresh one; that one's first beat is the recovery mail.
# A beat counts for the room when its pid is the room capture's. A beat with NO pid (a capture
# started before beats carried one, adopted across the upgrade; or the legacy voice_mode.listen)
# counts when its source is `mic` and its session is the room capture's --session. A call leg's
# beats (source call:N, its own pid) never count. The clock starts when the keeper first sees the
# capture, so a young or just-adopted capture has the full grace before its first beat.
BEAT_STALE="${EARS_BEAT_STALE:-120}"; BEAT_KILL="${EARS_BEAT_KILL:-300}"
beat_pid=""; beat_session=""; beat_since=0; beat_last=0; beat_dev=""
beat_hhmm() { date -r "$1" +%H:%M 2>/dev/null || date -d "@$1" +%H:%M; }   # $1 = epoch s
beat_track() {  # $1 = the room capture's pid ("" when none runs); call it every loop, first.
  # $2 = when its clock starts (epoch s): adoption passes its last beat; default now.
  [ "$1" = "$beat_pid" ] && return 0
  beat_pid="$1"; beat_since="${2:-$(deaf_now)}"; beat_last=0; beat_dev=""; beat_session=""
  [ -n "$1" ] && beat_session="$(capture_arg --session)" && beat_dev="$(capture_arg --device)"
  return 0
}
beat_seen() {  # $1 pid ("-": none), $2 session, $3 source, $4 peak, $5 device: one heartbeat row
  [ -n "$beat_pid" ] || return 0
  if [ "$1" = - ]; then
    [ "$3" = mic ] && [ -n "$beat_session" ] && [ "$2" = "$beat_session" ] || return 0
  else
    [ "$1" = "$beat_pid" ] || return 0
  fi
  beat_last="$(deaf_now)"; beat_dev="$5"
  deaf_clear room beat "$5"
  zeros_seen "$beat_pid" "$4" "$5"   # its peak: the zeros region
  lid_beat "$4" "$5"                 # and the lid region (zeros first: one recovery mail)
}
beat_check() {  # judge the room capture by its last beat; call it every loop, after heard_watch
  [ -n "$beat_pid" ] || return 0
  local now ref age; now="$(deaf_now)"; ref="$beat_last"; [ "$ref" -gt 0 ] || ref="$beat_since"
  age=$(( now - ref ))
  [ "$age" -gt "$BEAT_STALE" ] || return 0
  deaf_set room beat "since $(beat_hhmm "$ref")" "$beat_dev"   # one mail per episode: a repeat is silent
  [ "$age" -gt "$BEAT_KILL" ] || return 0
  note "capture $beat_pid: no heartbeat for ${age}s, over EARS_BEAT_KILL (${BEAT_KILL}s): wedged, stopping it hard"
  stop_capture || note "capture $beat_pid did not stop"
  beat_pid=""   # the next loop arms on whatever runs then: a fresh capture, or none
}
# --- end beat ---
# --- launch: start the room capture with no shell left behind (tests/test_keeper_adopt.py runs this alone) ---
# Measured: `( cd && nohup X & )` forks a bash that keeps the KEEPER's argv and waits
# on X for X's whole life. The ( ) exits, so that bash is reparented to launchd: the "orphaned
# old keeper" (ppid 1) seen beside a wedged capture. `exec` replaces it,
# so the capture's own parent is launchd and no bash with the keeper's argv outlives the launch.
launch_capture() {  # $1 device, $2 stop file, $3 missed-reply flags ("" or two words)
  # $3 is unquoted on purpose: "" or two words, the second a checked number
  ( cd "$W" && exec nohup "$PY" -m "$MOD" capture \
      --source mic --device "$1" --heartbeat 15 --agent "${EARS_AGENT:-ears}" \
      --session "${EARS_SESSION:-${CLAUDE_CODE_SESSION_ID:-ears-keeper}}" --stop-file "$2" \
      --turn-text redecode --detector silero --silero-model "$SILERO" $3 \
      >>"$LOGDIR/ears-keeper-capture.log" 2>&1 & )
}
# --- end launch ---
start_capture() {  # $1 = device
  model_ready "$1" || return 1   # no model: held, said once (deaf_set), never a relaunch loop
  STOP="$RUN/STOP-capture-keeper-$(date +%H%M%S)"
  note "starting capture on $1, STOP=$STOP"
  local s; s="$(missed_reply_window)"
  [ "$s" = "${EARS_MISSED_REPLY_S:-30}" ] || note "EARS_MISSED_REPLY_S=${EARS_MISSED_REPLY_S:-} is not seconds: missed-reply clips off"
  note "missed-reply clips: window ${s}s (0 = off; EARS_MISSED_REPLY_S)"
  # Whether any of it is KEPT is the recording switch's, off by default. The
  # capture inherits EARS_RECORD and resolves it itself; the keeper never passes --record.
  local rec; rec="$("${PY:-false}" -m ears.recording status --line 2>/dev/null)" || rec="unknown (ears recording status failed)"
  note "recording: ${rec:-unknown} (EARS_RECORD, then ears.json \"record\", then off; audio is kept only while it is on)"
  local missed="--missed-reply-window $s"
  launch_capture "$1" "$STOP" "$missed"; room_input="$1"   # the input a restart wait belongs to (stt region)
  sleep 3
  # While the room is DEAF these are notes: the DEAF mail was the one mail (deaf region).
  if capture_running; then
    mail_unless_deaf "ears: capture restarted on $1 at $(date +%H:%M) EOM"
    model_started "$1"   # a model DEAF episode ends here: its recovery is the one mail
  else mail_unless_deaf "ears: capture restart on $1 FAILED at $(date +%H:%M) EOM"; fi
}
# --- stop: stopping the room capture, STOP file then SIGTERM then SIGKILL (tests/test_keeper_stop.py runs this alone) ---
# A wedged capture once ignored its STOP file AND SIGTERM. Its stop-file check
# and its SIGTERM handler both run inside its frame loop (src/ears/listen/loop.py, __main__.py),
# so a loop that blocks hears neither. Only SIGKILL ended it, by hand; the keeper used to touch
# the STOP file, wait 15 s and give up, every loop, forever. Now every path that stops the room
# capture (listening off, a call live, the FOLLOW swap, a stopped heartbeat) escalates. A healthy
# capture honours its STOP file within 0.3 s, so SIGTERM and SIGKILL only ever reach a wedged one.
# It acts only on the keeper's own room capture (capture_pid: the STOP-capture-keeper- file),
# never on a call leg or a foreign capture.
STOP_WAIT="${EARS_STOP_WAIT:-15}"; TERM_WAIT="${EARS_TERM_WAIT:-5}"
capture_gone() {  # $1 pid, $2 seconds -> 0 as soon as $1 is no longer the room capture
  local n=$(( $2 * 5 ))
  until [ "$(capture_pid)" != "$1" ]; do
    [ "$n" -gt 0 ] || return 1
    n=$(( n - 1 )); sleep 0.2
  done
}
stop_capture() {  # stop the room capture; 0 once it is gone, 1 if it could not be stopped
  local pid stop; pid="$(capture_pid)"; [ -n "$pid" ] || return 0
  stop="$(capture_arg --stop-file)"
  if [ -z "$stop" ]; then note "running capture has no --stop-file; not stopping it"; return 1; fi
  note "stopping capture $pid (device $(capture_arg --device)) via $stop"
  touch "$stop"
  capture_gone "$pid" "$STOP_WAIT" && return 0
  note "capture $pid still running ${STOP_WAIT} s after its stop file: SIGTERM"
  kill -TERM "$pid" 2>/dev/null
  capture_gone "$pid" "$TERM_WAIT" && return 0
  note "capture $pid still running ${TERM_WAIT} s after SIGTERM: SIGKILL"
  kill -KILL "$pid" 2>/dev/null
  capture_gone "$pid" 2 && return 0
  note "capture $pid survived SIGKILL"; return 1
}
# The same escalation for an ear that is not the room's (stop_pid: a call leg, call-tap's
# far-end ear; the listen region below), by pid rather than by capture_pid.
pid_alive() {  # $1 pid -> 0 while it runs (a zombie has ended)
  local s; s="$(ps -o stat= -p "$1" 2>/dev/null | tr -d ' ')"
  [ -n "$s" ] && [ "${s#Z}" = "$s" ]
}
pid_gone() {  # $1 pid, $2 seconds -> 0 as soon as it has ended
  local n=$(( $2 * 5 ))
  while pid_alive "$1"; do
    [ "$n" -gt 0 ] || return 1
    n=$(( n - 1 )); sleep 0.2
  done
}
pid_arg() {  # $1 pid, $2 flag -> the word after the flag in its argv, or nothing
  ps -ww -o args= -p "$1" 2>/dev/null | tr ' ' '\n' | grep -A1 -x -- "$2" | tail -1
}
stop_pid() {  # $1 pid, $2 what it is, $3 its stop file ("" for none): the stop file, then
  # SIGTERM, then SIGKILL, as stop_capture escalates. 0 once it has ended, 1 if it would not
  local pid="$1" what="$2" stop="${3:-}"
  pid_alive "$pid" || return 0
  if [ -n "$stop" ]; then
    note "stopping $what $pid via $stop"
    touch "$stop"
    pid_gone "$pid" "$STOP_WAIT" && return 0
    note "$what $pid still running ${STOP_WAIT} s after its stop file: SIGTERM"
  else
    note "stopping $what $pid (SIGTERM)"
  fi
  kill -TERM "$pid" 2>/dev/null
  pid_gone "$pid" "$TERM_WAIT" && return 0
  note "$what $pid still running ${TERM_WAIT} s after SIGTERM: SIGKILL"
  kill -KILL "$pid" 2>/dev/null
  pid_gone "$pid" 2 && return 0
  note "$what $pid survived SIGKILL"; return 1
}
# --- end stop ---
# --- listen: listen:false stops every ear (tests/test_keeper_listen_off.py runs this alone) ---
# Off means nothing listens. `listen: false` in
# ears.json stops the room capture, our call-ears, every call leg (`--source call:N`) and
# call-tap's far-end ear, in FOLLOW and pinned-DEVICE mode alike, and starts none until it is
# true again. It once stopped the room capture only, and only in FOLLOW mode.
# - call-ears goes FIRST: it restarts an ear that dies while its call is live. It is stopped by
#   signal, never by $CALL_EARS_DIR/STOP: that file is the human's stop, and it would block the
#   resume. A SIGTERMed call-ears leaves its legs running (its cleanup runs on STOP only), so
#   they are stopped next, each by its stop file if it has one, else SIGTERM, then SIGKILL.
# - call-tap (bin/call-tap, docs/call-tap.md): its sox passthrough carries the user's own voice
#   OUT to the call app and keeps running; only its far-end capture stops (so the user's
#   voice still reaches the call). The keeper touches $TAP_RUN/LISTEN-OFF, then STOP-ear, that capture's own stop file;
#   call-tap holds the ear paused while LISTEN-OFF is there. listen:true removes LISTEN-OFF and
#   the ear comes back. A keeper that dies while off leaves it paused: that fails closed.
# - listen:true: calls_up starts call-ears, whose main() gives every live call an ear, and the
#   loop starts the room as before. The loop notes and mails each change ONCE; ears_off says
#   nothing on a loop that finds nothing to stop.
# ears_off runs every loop while off (and at a start that is off), so an ear started behind the
# keeper's back is stopped within a loop too.
TAP_RUN="${EARS_CALL_TAP_RUN:-${XDG_STATE_HOME:-$HOME/.local/state}/ears/call-tap}"   # bin/call-tap's RUN
off_other=""
call_legs() {  # the running call-leg captures (`--source call:N`), one pid per line
  local p args
  for p in $(pgrep -f "$CAPTURE_PAT"); do
    args="$(ps -ww -o args= -p "$p" 2>/dev/null)" || continue
    case "$args" in *"--source call:"*) echo "$p" ;; esac
  done
}
calls_off() {  # stop our call-ears and wait until it has ended, so it restarts no leg stopped next
  local p="$calls_pid" o
  if [ -n "$p" ]; then
    note "listening off: stopping call ears $p"
    calls_down                                   # SIGTERM; forgets the pid, so calls_up starts afresh
    pid_gone "$p" "$TERM_WAIT" || { kill -KILL "$p" 2>/dev/null; pid_gone "$p" 2; }
    wait "$p" 2>/dev/null                        # our child: reap it
  fi
  o="$(other_call_ears)"   # the old ears-call job's: not ours to stop, so say so, once
  if [ -n "$o" ] && [ "$o" != "$off_other" ]; then
    note "listening off: another job runs call ears, which this keeper cannot stop: $o"
  fi
  off_other="$o"
  return 0
}
tap_ear() {  # call-tap's far-end capture, when one runs: the pid its capture.pid names, if
  # that process's argv carries call-tap's own STOP-ear (a stale pid file names no one)
  local p; p="$(cat "$TAP_RUN/capture.pid" 2>/dev/null)"
  [[ "$p" =~ ^[0-9]+$ ]] && pid_alive "$p" || return 0
  case "$(ps -ww -o args= -p "$p" 2>/dev/null)" in *"--stop-file $TAP_RUN/STOP-ear"*) echo "$p" ;; esac
}
tap_off() {  # pause call-tap's far-end ear; its passthrough runs on
  local p
  if [ ! -e "$TAP_RUN/LISTEN-OFF" ]; then
    mkdir -p "$TAP_RUN" && touch "$TAP_RUN/LISTEN-OFF"   # first: call-tap must not restart the ear
  fi
  p="$(tap_ear)"
  [ -n "$p" ] && stop_pid "$p" "call-tap's far-end ear (its passthrough runs on)" "$TAP_RUN/STOP-ear"
  return 0
}
tap_on() {  # call-tap's far-end ear may run again
  [ -e "$TAP_RUN/LISTEN-OFF" ] || return 0
  rm -f "$TAP_RUN/LISTEN-OFF" && note "listening on: call-tap's far-end ear may run again"
}
ears_off() {  # listen:false: stop every ear and start none. Call-ears first, then its legs
  local p
  calls_off
  for p in $(call_legs); do stop_pid "$p" "call leg" "$(pid_arg "$p" --stop-file)"; done
  tap_off
  if capture_running; then stop_capture; fi
  return 0
}
ears_on() {  # listen:true: the ears that were stopped may start again
  tap_on
  calls_up
}
# --- end listen ---
# --- adopt: a keeper restart adopts only a capture that still beats (tests/test_keeper_adopt.py runs this alone) ---
# Once a kickstarted keeper logged "adopting running capture 78317" and adopted
# a capture whose heartbeat had been dead for hours: it checked only that the pid lived.
# Now it reads the capture's last heartbeat from the tail of the heard log (today's, else
# yesterday's, for just after midnight), by its pid, or, for a beat with no pid (a capture from
# before beats carried one), by source `mic` and the room capture's --session.
# With no beat at all, the capture's own age (ps etime) stands in. Then, by these thresholds:
# - within EARS_BEAT_KILL: adopted, with no mail. The beat tracker's clock starts at that last
#   beat, so one older than EARS_BEAT_STALE is reported DEAF on the first loop, as it would have
#   been had this keeper been watching all along;
# - older than EARS_BEAT_KILL: wedged. Not adopted: one DEAF mail (deaf_set room beat), a hard
#   stop (stop_capture), and the loop starts a fresh capture, whose first beat is the recovery.
BEAT_TAIL="${EARS_BEAT_TAIL:-262144}"   # bytes of each log read back: 15 s beats, ~0.5 KB a line
last_beat() {  # $1 pid, $2 session ("" for none) -> "EPOCH<TAB>DEVICE" of that capture's newest
  # heartbeat in the tail of today's heard log, else yesterday's; nothing when there is none
  python3 -c 'import datetime,json,os,sys
d,tail,pid,sess=sys.argv[1],int(sys.argv[2]),sys.argv[3],sys.argv[4]
today=datetime.date.today()
for day in (today,today-datetime.timedelta(days=1)):
  try:
    with open(os.path.join(d,"heard_%s.jsonl" % day.isoformat()),"rb") as f:
      f.seek(0,2); n=f.tell(); f.seek(max(0,n-tail)); b=f.read()
  except OSError: continue
  for raw in reversed(b.splitlines()):
    if b"heartbeat" not in raw: continue
    try: r=json.loads(raw)
    except ValueError: continue
    if not isinstance(r,dict) or r.get("kind")!="event" or r.get("event")!="heartbeat": continue
    p=r.get("pid")
    if type(p) is int and p>0:
      if str(p)!=pid: continue
    elif not (sess and r.get("source")=="mic" and r.get("session")==sess): continue
    try: t=datetime.datetime.fromisoformat(str(r.get("ts"))).timestamp()
    except ValueError: continue
    print("%d\t%s" % (t,str(r.get("device") or "?").replace("\t"," ").replace("\n"," ")[:20])); sys.exit(0)' \
    "$HEARD_DIR" "$BEAT_TAIL" "$1" "$2" 2>/dev/null
}
proc_age() {  # $1 pid -> seconds it has run, from ps etime ([[dd-]hh:]mm:ss); nothing when gone
  local e d=0 h=0 m s a b c; e="$(ps -o etime= -p "$1" 2>/dev/null | tr -d ' ')"; [ -n "$e" ] || return 0
  case "$e" in *-*) d="${e%%-*}"; e="${e#*-}" ;; esac
  IFS=: read -r a b c <<<"$e"
  if [ -n "$c" ]; then h="$a"; m="$b"; s="$c"; else m="$a"; s="$b"; fi
  echo $(( 10#$d * 86400 + 10#$h * 3600 + 10#$m * 60 + 10#$s ))
}
adopt_capture() {  # $1 = the room capture running at keeper start -> 0 adopted; 1 wedged, stopped
  local pid="$1" session dev lb ref age how now start=""; now="$(deaf_now)"
  session="$(capture_arg --session)"; dev="$(capture_arg --device)"
  lb="$(last_beat "$pid" "$session")"
  age="$(proc_age "$pid")"; [ -n "$age" ] && start=$(( now - age ))
  # A beat older than the capture is not its own: a pid-less one matched only by session can be
  # an earlier capture's (the one before an upgrade), and must not condemn a young capture that
  # has not beat yet. 2 s of slack for etime's whole seconds.
  if [ -n "$lb" ] && [ -n "$start" ] && [ "${lb%%$'\t'*}" -lt $(( start - 2 )) ]; then
    note "capture $pid started after its last matching heartbeat ($(beat_hhmm "${lb%%$'\t'*}")): not its own"
    lb=""
  fi
  if [ -n "$lb" ]; then
    ref="${lb%%$'\t'*}"; dev="${lb#*$'\t'}"; how="last heartbeat $(beat_hhmm "$ref")"
  else
    ref="${start:-$now}"; how="no heartbeat since it started at $(beat_hhmm "$ref")"
  fi
  age=$(( now - ref )); [ "$age" -ge 0 ] || age=0
  if [ "$age" -le "$BEAT_KILL" ]; then
    note "adopting running capture $pid: device '$dev', stop $(capture_arg --stop-file); $how, ${age}s ago"
    beat_track "$pid" "$ref"
    return 0
  fi
  note "not adopting wedged capture $pid ($how, ${age}s ago, over EARS_BEAT_KILL ${BEAT_KILL}s)"
  deaf_set room beat "since $(beat_hhmm "$ref")" "$dev"
  stop_capture || note "wedged capture $pid did not stop"
  return 1
}
# --- end adopt ---
# --- pidfile: one keeper loop at a time (tests/test_keeper_adopt.py runs this alone) ---
# The keeper's input-watch and call-ears already had pidfiles, so a new keeper stops the old
# one's. The loop itself had none. Now a keeper names itself in $RUN/ears-keeper.pid at start.
# If that file names another live keeper loop (a shell running an ears-keeper.sh, not this
# one), the new keeper TERMs it, waits EARS_KEEPER_TERM_WAIT s (5), and KILLs it if need be.
KEEPER_PIDF="$RUN/ears-keeper.pid"
is_keeper() {  # $1 pid -> 0 when it is a shell running an ears-keeper.sh (not `less ears-keeper.sh`)
  local a; a="$(ps -ww -o args= -p "$1" 2>/dev/null)" || return 1
  set -- $a
  case "${1:-}" in *sh) ;; *) return 1 ;; esac
  case "${2:-}" in ears-keeper.sh|*/ears-keeper.sh) return 0 ;; esac
  return 1
}
keeper_claim() {  # at start: end the keeper loop the pidfile names (never this one), then name us
  local old n; old="$(cat "$KEEPER_PIDF" 2>/dev/null)"
  if [[ "$old" =~ ^[0-9]+$ ]] && [ "$old" != "$$" ] && is_keeper "$old"; then
    note "another keeper loop runs, pid $old: stopping it (SIGTERM)"
    kill -TERM "$old" 2>/dev/null
    n=$(( ${EARS_KEEPER_TERM_WAIT:-5} * 5 ))
    while [ "$n" -gt 0 ] && kill -0 "$old" 2>/dev/null; do n=$(( n - 1 )); sleep 0.2; done
    if kill -0 "$old" 2>/dev/null; then note "keeper $old still running after SIGTERM: SIGKILL"; kill -KILL "$old" 2>/dev/null; fi
  fi
  echo "$$" >"$KEEPER_PIDF"
}
keeper_release() { [ "$(cat "$KEEPER_PIDF" 2>/dev/null)" = "$$" ] && rm -f "$KEEPER_PIDF"; return 0; }
# --- end pidfile ---

keeper_claim   # first: a loop the last keeper left running is ended before anything else starts
# A keeper restarted under AbandonProcessGroup leaves its watcher behind: stop it.
if [ -s "$WATCH_PIDF" ]; then
  old="$(cat "$WATCH_PIDF")"
  ps -o command= -p "$old" 2>/dev/null | grep -qF -- "$WATCH_BIN" && kill "$old" 2>/dev/null && note "stopped the last keeper's input-watch $old"
fi
# Likewise its call-ears: the new one adopts the call ears it left running (call-ears captured()).
if [ -s "$CALLS_PIDF" ]; then
  old="$(cat "$CALLS_PIDF")"
  ps -o command= -p "$old" 2>/dev/null | grep -qF -- "$CALL_EARS" && kill "$old" 2>/dev/null && note "stopped the last keeper's call-ears $old"
  rm -f "$CALLS_PIDF"
fi
trap 'watch_down; calls_down; keeper_release; exit 0' TERM INT
trap 'watch_down; calls_down; keeper_release' EXIT
watch_up
last_l="$(listen_on)"   # before any ear starts: a keeper started with listen:false starts none
if [ "$last_l" = 0 ]; then
  note "listening OFF by ears.json: starting no ear (no room capture, no call ears) until it is true"
  ears_off   # and stops any the last keeper left running: off means nothing listens
else
  ears_on
fi
prev="$(inputs)"; builtin_refresh "$prev"; seen=0; last_t="$(target)"; last_s="$(stt_conf)"; out_due=0; loops=0
last_c=""   # so a call already live at start is announced, and the room stops at once
note "ears-keeper: $([ $FOLLOW = 1 ] && echo "FOLLOWING the default input (now '$last_t')" || echo "device '$DEVICE'"), every ${INTERVAL}s, stable ${STABLE}; inputs: $(echo "$prev" | paste -sd, -)"
foreign_captures | while IFS= read -r l; do note "not the room's capture, left alone: $l"; done
heard_start   # before adoption, so no beat written while it decides is missed
if capture_running; then  # adopt it only if it still beats (adopt region), then check it survived:
  # once a keeper adopted a capture that launchd was killing with the old job's process
  # group; the loop restarted it 3 s later.
  adopted="$(capture_pid)"
  if adopt_capture "$adopted"; then
    sleep 2
    kill -0 "$adopted" 2>/dev/null || note "adopted capture $adopted died within 2 s; the loop will start a fresh one"
  fi
fi
check_output
while :; do
  sleep "$INTERVAL"
  watch_up
  l="$(listen_on)"   # first: while it is off, nothing below may start an ear (listen region)
  if [ "$l" != "$last_l" ]; then   # once per change, never once per loop
    note "listen: $last_l -> $l (ears.json)"
    mail_me "ears: listening $([ "$l" = 1 ] && echo ON || echo OFF) by ears.json at $(date +%H:%M) EOM"
    last_l="$l"; seen=0
  fi
  if [ "$l" = 0 ]; then ears_off; else ears_on; fi   # ears_on keeps call-ears up, as calls_up did here
  beat_track "$(capture_pid)"
  heard_watch   # first, ahead of every skip below: a call leg's capture can fall behind while the room is off,
  beat_check    # and the room capture's heartbeat is judged whatever the loop does next
  zeros_check   # a capture that gave only zeros is reopened, once (zeros region)
  loops=$((loops + 1))
  if [ "$out_due" -gt 0 ]; then out_due=$((out_due - 1)); [ "$out_due" = 0 ] && check_output
  elif [ "$OUT_EVERY" -gt 0 ] && [ $((loops % OUT_EVERY)) = 0 ]; then check_output; fi
  now="$(inputs)"; [ "$now" = "$builtin_for" ] || builtin_refresh "$now"; t="$(target)"
  if [ "$now" != "$prev" ]; then
    added="$(comm -13 <(echo "$prev") <(echo "$now") | paste -sd, -)"
    removed="$(comm -23 <(echo "$prev") <(echo "$now") | paste -sd, -)"
    note "change: +[$added] -[$removed]"
    $HEARD write event device-change --device "${t:-none}" --text "added: ${added:-none}; removed: ${removed:-none}" >/dev/null \
      || note "heard write FAILED"
    if [ "$FOLLOW" = 0 ]; then
      if grep -qxi "$DEVICE" <<<"$removed"; then mail_me "ears: $DEVICE gone at $(date +%H:%M); capture will stop EOM"; fi
      if grep -qxi "$DEVICE" <<<"$added"; then mail_me "ears: $DEVICE back at $(date +%H:%M); restart when stable EOM"; fi
    fi
    prev="$now"; out_due=$((STABLE > 0 ? STABLE : 1))   # >= 1, or EARS_STABLE=0 skips it
  fi
  [ "$l" = 0 ] && continue   # off: ears_off stopped every ear at the top of this loop; start none
  c="$(call_live)"        # a live call: the room is off until hang-up (call_live says why)
  if [ "$c" != "$last_c" ]; then
    if [ -n "$c" ]; then
      note "call live ($c): room mic OFF until hang-up"
      $HEARD write event room-off --device "$c" --text "call live: the room mic is off until hang-up" >/dev/null \
        || note "heard write FAILED"
      mail_me "ears: room mic OFF while $c is live, $(date +%H:%M) EOM"
    else
      note "no call live ($last_c ended): room mic back"
      $HEARD write event room-on --device "${t:-none}" --text "the call ended: the room mic is back" >/dev/null \
        || note "heard write FAILED"
      mail_me "ears: room mic back on, the call ended, $(date +%H:%M) EOM"
    fi
    last_c="$c"; seen=0
  fi
  if [ -n "$c" ]; then
    if capture_running; then stop_capture; fi
    continue
  fi
  # The engines (ears.json final and partials) changed - by hand or from the phone. The capture
  # follows them itself (FollowingSTT reads ears.json before every transcribe; the partials
  # engine is looked at as each turn opens), so there is no restart: a restart cost a ~4 s gap
  # in what the room heard. Say so, that is all.
  st="$(stt_conf)"
  if [ "$st" != "$last_s" ]; then
    note "engine: '$last_s' -> '$st' (ears.json final, partials); the capture follows it, no restart"
    last_s="$st"
    if capture_running; then
      mail_me "ears: engine changed by ears.json at $(date +%H:%M); capture follows EOM"
    fi
  fi
  lid_watch "$t"   # the lid first, before the FOLLOW mail (lid region)
  if [ "$FOLLOW" = 1 ] && [ "$t" != "$last_t" ]; then
    note "ears input: '$last_t' -> '$t'"
    if lid_deaf; then note "not mailed, the room is DEAF (lid): ears: mic -> ${t:-none}"   # one mail each way
    else mail_me "ears: mic -> ${t:-none} at $(date +%H:%M) EOM"; fi  # was "(was X)": 71 chars, refused
    restart_reset "$t"   # a new device: no backoff left over from the last one (stt region)
    last_t="$t"; seen=0
  fi
  if [ -n "$t" ] && grep -qxiF -- "$t" <<<"$now"; then seen=$((seen + 1)); else seen=0; fi
  if [ "$lid_on" = 1 ]; then   # the lid is shut on the built-in: HOLD, start nothing (lid region)
    # A capture on the built-in stops at once. One still on the input the Mac left
    # stops once the move has held STABLE loops, as the FOLLOW swap would have stopped it: a
    # one-loop flap of the default input does not cut a capture that hears.
    if capture_running && { lid_capture_builtin "$(capture_arg --device)" || [ "$seen" -ge "$STABLE" ]; }; then
      note "lid shut: stopping the room capture, starting none"; stop_capture
    fi
    continue
  fi
  [ "$seen" -ge "$STABLE" ] || continue
  if capture_running; then
    # FOLLOW only: a capture on the wrong device is swapped for one on the default.
    cur="$(capture_arg --device)"
    if [ "$FOLLOW" = 1 ] && [ -n "$cur" ] && ! grep -qiF -- "$cur" <<<"$t"; then
      stop_capture && start_capture "$t"
    fi
  else
    restart_due "$t" && start_capture "$t"   # held while a capture that heard nothing backs off (stt region)
  fi
done
