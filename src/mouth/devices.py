"""``mouth devices``: the outputs ``--device`` takes, the current output and input, the volume.

The text form stays "one name ``--device`` takes per line". Notes ride after a
TAB as ``# ...``, as a live call's line always has, so a reader that cuts at
the tab still gets names:

    Mac Studio Speakers	# current output, volume 62%
    External Headphones
    call:3	# live call, sessionvoice+super
    # current input: MacBook Pro Microphone

The current input is not a device ``--device`` takes, so it is not a list
line: it is one trailing comment line, starting with ``#``, with no name
before it. ``--json`` carries the same facts as one object.

The current devices and the volume come from CoreAudio, live
(:mod:`mouth.coreaudio`). Only the output's name falls back, to PortAudio's
default, which can be stale after a switch. Unknown is left out of the text and
``null`` in the JSON, never invented.
"""

from __future__ import annotations

from typing import Optional

from . import coreaudio, output


def _portaudio_default() -> Optional[str]:
    """PortAudio's default output name (cached at init, so maybe stale), or None."""
    try:
        sd = output._sd()
        with output._PA:
            idx = sd.default.device[1]
            if idx is None or idx < 0:
                return None
            return sd.query_devices(idx)["name"] or None
    except Exception:
        return None


def devices() -> dict:
    """Everything ``mouth devices`` reports, unknowns as None."""
    outputs = output.output_names(rescan=False)
    calls = [{"name": f"call:{c}", "who": who} for c, who, _ in output.live_calls()]
    current = coreaudio.default_output_name() or _portaudio_default()
    return {"outputs": outputs, "calls": calls,
            "current_output": current,
            "current_input": coreaudio.default_input_name(),
            "output_volume": coreaudio.output_volume(),
            "output_muted": coreaudio.output_muted()}


def _output_note(d: dict) -> str:
    note = "# current output"
    vol, muted = d.get("output_volume"), d.get("output_muted")
    if vol is not None:
        note += f", volume {round(vol * 100)}%" + (" (muted)" if muted else "")
    elif muted:
        note += ", muted"
    return note


def devices_text(d: dict) -> str:
    """The text form: one name per line, the current output marked, the input last."""
    current = (d.get("current_output") or "").lower()
    lines, marked = [], False
    for name in d["outputs"]:
        if current and not marked and name.lower() == current:
            lines.append(f"{name}\t{_output_note(d)}")
            marked = True
        else:
            lines.append(name)
    lines += [f"{c['name']}\t# live call, {c['who']}" for c in d["calls"]]
    if d.get("current_input"):
        lines.append(f"# current input: {d['current_input']}")
    return "\n".join(lines)
