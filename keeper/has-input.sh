#!/usr/bin/env bash
# has-input.sh NAME: exit 0 if macOS lists an audio device called NAME with
# input channels. Parses one device block (its header is at 8 spaces).
system_profiler SPAudioDataType | awk -v n="        $1:" '
  /^        [^ ].*:$/ { inblk = ($0 == n) ; next }
  inblk && /Input Channels/ { found = 1 }
  END { exit !found }'
