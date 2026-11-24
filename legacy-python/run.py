#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""便捷入口，等价于 ``python -m pclhome``。

    python run.py serve
    python run.py generate --offline
    python run.py doctor
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pclhome.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
