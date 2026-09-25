# -*- coding: utf-8 -*-
"""命令行入口。

    python -m pclhome serve                 启动主页服务（默认 127.0.0.1:8787）
    python -m pclhome serve --port 8080 --host 0.0.0.0
    python -m pclhome generate [--offline]  只生成静态 XAML
    python -m pclhome doctor [--offline]    体检
    python -m pclhome version               版本号
"""
from __future__ import annotations

import argparse
import sys

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pclhome",
        description="PCL2 个性化主页 · Python 复刻版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--version", action="version", version="pclhome " + __version__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动主页服务")
    serve.add_argument("--host", default=None, help="监听地址（默认取配置，127.0.0.1）")
    serve.add_argument("--port", type=int, default=None, help="监听端口（默认 8787）")
    serve.add_argument("-v", "--verbose", action="store_true", help="详细日志（等同 --log-level debug）")
    serve.add_argument("-q", "--quiet", action="store_true", help="安静模式（等同 --log-level warn）")
    serve.add_argument("--log-level", default=None, choices=["debug", "info", "warn", "error"],
                       help="日志级别（默认 info）")
    serve.add_argument("--log-file", default=None, help="同时把日志追加写入文件")

    gen = sub.add_parser("generate", help="生成 Custom.xaml")
    gen.add_argument("--offline", action="store_true", help="跳过必应壁纸等外部请求")

    doc = sub.add_parser("doctor", help="本地体检 + 线上探测")
    doc.add_argument("--offline", action="store_true", help="只做本地检查")
    doc.add_argument("--url", default="http://127.0.0.1:8787", help="要探测的服务地址")

    sub.add_parser("version", help="打印版本号")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in (None, "version"):
        print("pclhome " + __version__)
        if args.command is None:
            parser.print_help()
        return 0

    from .config import load_config
    config = load_config()

    if args.command == "generate":
        from .generate import generate
        generate(config, offline=args.offline)
        return 0

    if args.command == "doctor":
        from .doctor import main as doctor_main
        forwarded = ["--offline"] if args.offline else ["--url", args.url]
        return doctor_main(forwarded)

    if args.command == "serve":
        import dataclasses
        from . import log
        from .server import serve

        # 日志级别：--log-level > -v > -q > 默认 info
        level = args.log_level or ("debug" if args.verbose else ("warn" if args.quiet else "info"))
        log.setup(level, log_file=args.log_file)
        overrides = {}
        if args.host:
            overrides["host"] = args.host
        if args.port:
            overrides["port"] = args.port
        if overrides:
            config = dataclasses.replace(config, **overrides)
        serve(config, verbose=args.verbose)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
