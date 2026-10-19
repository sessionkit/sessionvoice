#!/usr/bin/env bash
# test-labels-unchanged.sh: the memory fix must not change a single speaker label.
#
# Relabels recorded heard lines (SEQ.wav + SEQ.json from --record-only) twice, with the SAME
# references and flags run.sh starts the side-car with:
#   1. the ORIGINAL speaker-sidecar2.py, `git show $BASE:speakers/speaker-sidecar2.py`
#      (BASE defaults to be3e68c, master before the fix was ported), byte for byte;
#   2. the FIXED speaker-sidecar2.py beside this script (or $FIXED).
# Each run uses --relabel and --logdir in a temporary dir, never the real conversations dir.
# It reads files only: no mic is opened (--relabel never imports sounddevice), no voice.
# It then compares seq -> kind, speaker, fracs, speech_s, why, and exits:
#   0  every line has the same label under both
#   1  any difference (the diff is printed), or a line is missing on either side
#   2  it could not run (missing input, a run failed, or a copy passed the memory cap)
#
# Memory: the ORIGINAL code's MLX buffer cache grows ~55 MB per new line length (the
# leak), so 78 lines in one process would pass 3 GB on a machine that is already swapping.
# Both sides therefore run in chunks of $CHUNK lines, one process at a time; each label() is
# independent of the ones before it (a fresh Nemotron state per call), so chunking does not
# change a label. A watchdog samples each copy's `footprint` and kills it above $CAP_MB.
#
# Env: PY (the side-car's python; default $SPEAKER_PY, else ./.venv/bin/python, as run.sh),
#      SEGS (a --record-only dir; default ./room), REFS (a dir holding refs/NAME.wav and,
#      optionally, refs-call/NAME.wav, as run.sh reads them; default this dir),
#      SPEAKER_AGENT_REFS (as run.sh), BASE, FIXED, CHUNK (15), CAP_MB (3000),
#      KEEP=1 to keep the temp dir.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel)
PY=${PY:-${SPEAKER_PY:-$HERE/.venv/bin/python}}
SEGS=${SEGS:-$HERE/room}
REFS=${REFS:-$HERE}
AGENTS=${SPEAKER_AGENT_REFS:-}
BASE=${BASE:-be3e68c}
FIXED=${FIXED:-$HERE/speaker-sidecar2.py}
CHUNK=${CHUNK:-15}
CAP_MB=${CAP_MB:-3000}

say() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { say "ERROR: $*"; exit 2; }

[[ -x $PY ]] || die "no python at $PY (set PY or SPEAKER_PY)"
[[ -d $SEGS ]] || die "no recorded lines at $SEGS (set SEGS to a --record-only dir)"
[[ -f $FIXED ]] || die "no fixed side-car at $FIXED"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/ears140-labels.XXXXXX")
CHILD=
cleanup() {
  [[ -n $CHILD ]] && kill "$CHILD" 2>/dev/null || true
  if [[ ${KEEP:-0} == 1 ]]; then say "kept $WORK"; else rm -rf "${WORK:?}"; fi
}
trap cleanup EXIT

git -C "$REPO" show "$BASE:speakers/speaker-sidecar2.py" >"$WORK/original.py" \
  || die "cannot read speakers/speaker-sidecar2.py at $BASE"
cp "$FIXED" "$WORK/fixed.py"
cmp -s "$WORK/original.py" "$WORK/fixed.py" && say "note: fixed and original are identical"

# the references, as run.sh passes them (cwd holds them, relative names, name order)
mkdir -p "$WORK/refs/refs" "$WORK/refs/refs-call"
ENROLL=()
shopt -s nullglob
for w in "$REFS"/refs/*.wav; do
  n=$(basename "$w" .wav); cp "$w" "$WORK/refs/refs/"; ENROLL+=(--enroll "$n=refs/$n.wav")
done
(( ${#ENROLL[@]} > 0 )) || die "no references: no $REFS/refs/*.wav"
for w in "$REFS"/refs-call/*.wav; do
  n=$(basename "$w" .wav); cp "$w" "$WORK/refs/refs-call/"; ENROLL+=(--call-enroll "$n=refs-call/$n.wav")
done
shopt -u nullglob

# the lines: SEQ.json with its SEQ.wav, numeric order, split into chunk dirs (symlinks)
SEQS=()
while IFS= read -r s; do SEQS+=("$s"); done < <(
  for j in "$SEGS"/*.json; do
    s=$(basename "$j" .json)
    [[ $s =~ ^[0-9]+$ && -f $SEGS/$s.wav ]] && echo "$s"
  done | sort -n)
N=${#SEQS[@]}
(( N > 0 )) || die "no SEQ.wav + SEQ.json pairs in $SEGS"
for i in "${!SEQS[@]}"; do
  c=$(printf '%03d' $(( i / CHUNK )))
  mkdir -p "$WORK/lines/$c"
  ln -s "$SEGS/${SEQS[$i]}.json" "$WORK/lines/$c/"
  ln -s "$SEGS/${SEQS[$i]}.wav" "$WORK/lines/$c/"
done
say "$N lines from $SEGS in chunks of $CHUNK; original = $BASE, fixed = $FIXED; cap $CAP_MB MB"

footprint_mb() {  # total footprint of a pid in MB, or empty
  # footprint(1) exits 66 on a pid it cannot read (gone, or mid-exec); under set -e +
  # pipefail that killed the whole script with a silent 66, so an unreadable sample is
  # just skipped (empty), as the watchdog below expects.
  { footprint "$1" 2>/dev/null || true; } | awk '/Footprint:/ {
    for (i = 1; i < NF; i++) if ($i == "Footprint:") { v = $(i + 1); u = $(i + 2); break }
    if (u == "GB") v *= 1024; else if (u == "KB") v /= 1024; else if (u == "B") v /= 1048576
    printf "%d\n", v; exit }'
}

run_side() {  # run_side original|fixed: relabel every chunk, one process at a time
  local side=$1 peak=0 fp rc
  mkdir -p "$WORK/$side/logs"
  for d in "$WORK"/lines/*/; do
    ( cd "$WORK/refs" && exec env HF_HUB_OFFLINE=1 "$PY" "$WORK/$side.py" "${ENROLL[@]}" \
        --agent-refs "$AGENTS" --enroll-s 12 --relabel "$d" --logdir "$WORK/$side/logs" ) \
      >>"$WORK/$side/run.log" 2>&1 &
    CHILD=$!
    while kill -0 "$CHILD" 2>/dev/null; do
      fp=$(footprint_mb "$CHILD")
      if [[ -n $fp ]]; then
        (( fp > peak )) && peak=$fp
        if (( fp > CAP_MB )); then
          kill "$CHILD" 2>/dev/null; wait "$CHILD" 2>/dev/null || true; CHILD=
          die "$side: footprint $fp MB > cap $CAP_MB MB in chunk $(basename "$d") - killed it"
        fi
      fi
      sleep 0.5
    done
    rc=0; wait "$CHILD" || rc=$?; CHILD=
    if (( rc != 0 )); then
      grep -vi warning "$WORK/$side/run.log" | tail -20 >&2
      die "$side: relabel of chunk $(basename "$d") exited $rc"
    fi
  done
  say "$side: done, peak footprint $peak MB"
}

table() {  # table original|fixed: one sorted line per seq from every speakers_*.jsonl
  "$PY" - "$WORK/$1/logs" <<'EOF'
import json, sys
from pathlib import Path
rows = {}
for p in sorted(Path(sys.argv[1]).glob("speakers_*.jsonl")):
    for line in p.read_text().splitlines():
        r = json.loads(line)
        rows[int(r["seq"])] = (r["kind"], r["speaker"], json.dumps(r["fracs"], sort_keys=True), r["speech_s"],
                               r.get("why") or "")
for seq in sorted(rows):
    kind, who, fracs, speech, why = rows[seq]
    print(f"{seq}\t{kind}\t{who}\t{fracs}\t{speech}\t{why}")
EOF
}

run_side original
run_side fixed
table original >"$WORK/original.tsv"
table fixed >"$WORK/fixed.tsv"
no=$(wc -l <"$WORK/original.tsv" | tr -d ' '); nf=$(wc -l <"$WORK/fixed.tsv" | tr -d ' ')
say "labelled lines: original $no, fixed $nf, input $N"

fail=0
if (( no != N || nf != N )); then
  say "FAIL: a side did not label every input line"; fail=1
fi
if ! diff -u --label "original ($BASE)" --label "fixed" "$WORK/original.tsv" "$WORK/fixed.tsv"; then
  say "FAIL: labels differ (columns: seq kind speaker fracs speech_s why)"; fail=1
fi
if (( fail )); then exit 1; fi
say "PASS: $N/$N lines identical (seq kind speaker fracs speech_s why)"
cut -f3 "$WORK/fixed.tsv" | sort | uniq -c | sed 's/^/    /' >&2
