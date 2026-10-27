#!/bin/bash
# run.sh [--detach]: the speaker side-car on this machine, from its own directory.
#
# References: every refs/NAME.wav (made by make-refs.py on THIS mic), in name order.
# Calls:      every refs-call/NAME.wav (12 s of NAME heard over a call, cut from a bot's
#             caller-call.wav): call lines are named against these, never the room's.
#             None: call lines get no row, as before.
# Agents:     $SPEAKER_AGENT_REFS (e.g. ada,sam): enrolled AGENT voices, see below.
# Mic:        $SPEAKER_DEVICE: "follow" (the default) opens whatever device the
#             ears keeper's capture is on and moves with it; a device name pins it. The
#             side-car only listens while that capture runs.
# Python:     $SPEAKER_PY, else ./.venv/bin/python (needs mlx-audio, numpy, sounddevice).
# Name:       it runs as "ears-speakers" in ps -c, ps -o comm= and Activity Monitor, not
#             "Python": a copy of the interpreter's binary, made in its venv (see below).
# Out:        speakers_YYYY-MM-DD.jsonl beside the heard log; log in speakers.log.
# Stop:       touch STOP (it exits within a line). Never pkill -f.
# --detach:   start in its own session, so it outlives the shell or agent that ran it.
# Home:       $SPEAKER_HOME: where refs/, refs-call/, STOP and speakers.log live;
#             unset, this script's own directory, as before. `ears services` sets it to the
#             per-user speakers home, so the scripts can sit in a package (ears/_speakers/).
# Heard log:  $SPEAKER_LOGDIR: passed as --logdir when set, the heard log the ears
#             write (heard.log_dir()); unset, the side-car's own default, as before.
# Always on:  launchd/dev.sessionkit.voice.ears-speakers.plist.in runs this in the foreground, and
#             launchd restarts it after a crash or a reboot (STOP keeps it down).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "${SPEAKER_HOME:-$here}"
py=${SPEAKER_PY:-$here/.venv/bin/python}
dev=${SPEAKER_DEVICE:-follow}
# Agents whose voices are enrolled (a Kokoro voice from another machine, e.g. ada): over a
# line THIS machine's mouth is playing, they yield to that line's agent like "other" does.
agents=${SPEAKER_AGENT_REFS:-}
[ -x "$py" ] || { echo "run.sh: no python at $py (set SPEAKER_PY)" >&2; exit 1; }
shopt -s nullglob
refs=(refs/*.wav)
[ ${#refs[@]} -ge 1 ] || { echo "run.sh: no refs/*.wav - record and name them first (README)" >&2; exit 1; }
args=()
for w in "${refs[@]}"; do n=$(basename "$w" .wav); args+=(--enroll "$n=$w"); done
for w in refs-call/*.wav; do n=$(basename "$w" .wav); args+=(--call-enroll "$n=$w"); done
# The name (Activity Monitor would show plain "Python"). ps -c and Activity Monitor
# read the kernel's p_comm, which is the name of the FILE exec'd. Measured (Darwin 27,
# 2030-06-09): setproctitle rewrites argv only (ps -c says ears-speakers, p_comm stays
# "Python"); a symlink resolves to its target; exec -a sets argv[0] only; and a framework
# python (Homebrew, python.org) re-execs into Python.app/Contents/MacOS/Python, renaming it
# back. What works: a COPY of the binary the interpreter really runs as, named ears-speakers,
# in the venv's bin/ beside pyvenv.cfg (so the venv's packages still load), exec'd with
# argv[0] ears-speakers (ps -o comm= prints argv[0]). It is refreshed whenever it differs
# (a python upgrade). Not a venv, or bin/ not writable: plain $py, and a line saying so.
name=ears-speakers
run=$py
where=$("$py" -c 'import ctypes, os, sys
b = ctypes.create_string_buffer(4096)
try: n = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidpath(os.getpid(), b, 4096)
except OSError: n = 0
print(b.value.decode() if n > 0 else os.path.realpath(sys.executable))
print(os.path.join(sys.prefix, "bin") if sys.prefix != sys.base_prefix else "")') || where=
img=$(printf '%s\n' "$where" | sed -n 1p); bin=$(printf '%s\n' "$where" | sed -n 2p)
if [ -n "$bin" ] && [ -f "$img" ]; then
  if cmp -s "$img" "$bin/$name" || { cp "$img" "$bin/$name.$$" && mv -f "$bin/$name.$$" "$bin/$name"; }; then
    run=$bin/$name
  else
    rm -f "$bin/$name.$$"; echo "run.sh: could not copy $img to $bin/$name; running as $py" >&2
  fi
else
  echo "run.sh: $py is not a venv's python; it will show as its own name, not $name" >&2
fi
rm -f STOP
# the side-car by its absolute path: the cwd is the home, which need not hold the scripts.
# --logdir only when asked, so a hand setup's argv is as it was (bash 3.2: no empty "${a[@]}")
logdir=()
if [ -n "${SPEAKER_LOGDIR:-}" ]; then logdir=(--logdir "$SPEAKER_LOGDIR"); fi
cmd=("$run" "$here/speaker-sidecar2.py" --device "$dev" "${args[@]}" --agent-refs "$agents" --enroll-s 12 --ring 300 --stop-file STOP ${logdir[@]+"${logdir[@]}"})
if [ "${1:-}" = "--detach" ]; then
  "$py" -c 'import os, sys
if os.fork(): sys.exit(0)
os.setsid()
fd = os.open("speakers.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
os.dup2(fd, 1); os.dup2(fd, 2); os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
os.execv(sys.argv[1], [sys.argv[2]] + sys.argv[3:])' "${cmd[0]}" "$name" "${cmd[@]:1}"
  echo "run.sh: started in its own session; log speakers.log; stop: touch $PWD/STOP"
else
  exec -a "$name" "${cmd[@]}"
fi
