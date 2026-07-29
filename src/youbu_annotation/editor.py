#!/usr/bin/env python3
"""Launch the Youbu gait annotation desktop editor."""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .constants import APP_NAME, VERSION
from .gui import AnnotationEditor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", nargs="?", type=Path, help="可选的 T01-T04 原始试次 CSV")
    parser.add_argument("--annotations", type=Path, help="可选的聚合标注 CSV")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    return parser.parse_args()


def main() -> int:
    if platform.system() != "Linux":
        print("当前版本仅正式支持 Linux", file=sys.stderr)
        return 2
    args = parse_args()
    pg.setConfigOptions(antialias=False, background="#FAFAFA", foreground="#37474F")
    application = QtWidgets.QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(VERSION)
    window = AnnotationEditor()
    window.show()
    if args.input_csv:
        QtCore.QTimer.singleShot(0, lambda: window.open_trial(args.input_csv, args.annotations))
    else:
        QtCore.QTimer.singleShot(0, window.request_open)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
