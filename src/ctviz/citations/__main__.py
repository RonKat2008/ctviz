"""`python -m ctviz.citations` forwards to the offline verifier CLI (§11.8): the canonical
invocation is `python -m ctviz.citations.verify <response.json>`, which runs `verify.py` as
`__main__` directly and never imports this module; this file exists only so the shorter
`python -m ctviz.citations <response.json>` form also works."""

from ctviz.citations.verify_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
