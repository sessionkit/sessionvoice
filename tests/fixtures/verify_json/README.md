# verify_json: sessionmail's verdict goldens

Byte-for-byte copies of sessionmail's `tests/fixtures/verify_json/*.json`
(SM-788, schema 1): what `message verify --json` prints for each case, one
object on one line. `tests/mouth/test_mouth_mail_trust_uses.py` feeds each
to `mouth.box.parse_verdict` with its own `rc`, so the mouth's reading of
the contract is pinned to the verifier's own output. Only the `--trust`
goldens whose verdict is FULL or UNBLESSED (15, 16, 23) are signed; every
other one, plain mode included, is not.

If sessionmail adds or changes a golden, copy it here again.
