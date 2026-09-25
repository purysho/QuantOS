"""Entry point of the packaged QuantOS desktop app."""

import multiprocessing
import sys

USAGE = """QuantOS — free, open-source investment research (Apache-2.0)

Usage: QuantOS [--no-browser] [--smoke-test] [--version] [--help]

Starts the control center on this computer (127.0.0.1 only) and opens it in
your web browser. Keep this window open while you use QuantOS; close
it, or press Quit in the control center, to stop.

  --no-browser   print the link instead of opening a browser
  --smoke-test   run a self-test and exit (used by release builds)
  --version      print the version and exit

Data lives in your user profile, or in a "QuantOS-data" folder next to
this program if you create one (portable mode, e.g. on a USB stick)."""


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(USAGE)
        return 0
    if "--version" in args:
        from quantos import __version__

        print(f"QuantOS {__version__}")
        return 0
    unknown = [a for a in args if a not in ("--no-browser", "--smoke-test") and not a.startswith("-psn_")]
    if unknown:  # (-psn_ is added by older macOS launchers)
        print(f"Unknown option(s): {' '.join(unknown)}\n\n{USAGE}")
        return 2
    from quantos.app import app_command

    return app_command(open_browser="--no-browser" not in args, smoke_test="--smoke-test" in args)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # last resort: show the error before a console window closes
        print(f"QuantOS could not start: {exc}")
        if sys.platform == "win32" and "--smoke-test" not in sys.argv:
            input("Press Enter to close…")
        raise
