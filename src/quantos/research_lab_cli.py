from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .research_lab_demo import run_golden_research_lab


def demo(root: str | None) -> int:
    if root is None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_golden_research_lab(Path(tmp))
    else:
        result = run_golden_research_lab(Path(root))
    print(
        json.dumps(
            result.__dict__,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="quantos-lab")
    sub = parser.add_subparsers(dest="command", required=True)
    demo_parser = sub.add_parser(
        "demo",
        help="run the deterministic end-to-end Research Lab golden workflow",
    )
    demo_parser.add_argument(
        "--root",
        help="optional directory for persistent demo ledgers",
    )
    args = parser.parse_args()
    from .home import activate

    activate()
    if args.command == "demo":
        return demo(args.root)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
