#!/usr/bin/env bash
# heard-tail: follow today's heard log, one readable line per event (for the operator's screen).
# The inline-Python version broke (a \" inside bash single quotes reaches Python as a
# bare backslash: SyntaxError). The working code is heard-tail.py beside this file.
exec python3 "$(dirname "$0")/heard-tail.py" "$@"
