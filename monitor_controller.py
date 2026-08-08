#!/usr/bin/env python3
"""红米 G Pro 27U Toolbox 兼容启动入口。"""

from mimonitor_toolbox.app import main
from mimonitor_toolbox.main_window import App

__all__ = ["App", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
