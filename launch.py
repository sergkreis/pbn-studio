# SPDX-License-Identifier: GPL-3.0-only
"""Development launcher. Does not alter installed executables."""
from pbnstudio.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
