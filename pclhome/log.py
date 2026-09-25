# -*- coding: utf-8 -*-
"""日志：时间戳 + 级别，可选同时写文件。

为什么不直接用标准库 logging：这个项目里所有输出都是"某模块发生了什么"的短句，
用 ``logging`` 要配 handler/formatter 反而更绕。这里只要三件事——时间、级别、能写文件，
自己写一个二十行的实现就够了，也让 ``out()`` 能无痛替换掉散落各处的 ``print()``。

级别：

    DEBUG  逐请求细节、缓存命中、模板重载、外部请求耗时
    INFO   每个请求一行、启动信息、内容更新（默认）
    WARN   外部请求失败、图标抓不到、模板缺失等"能继续跑但要注意"
    ERROR  配置错误、组装失败

用法：

    from .log import out, warn, info, debug
    out("[Net] 请求失败：" + url)          # INFO
    warn("[Net] 请求失败：" + url)         # WARN
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from datetime import datetime

DEBUG, INFO, WARN, ERROR = 10, 20, 30, 40

_NAMES = {DEBUG: "DEBUG", INFO: "INFO", WARN: "WARN", ERROR: "ERROR"}

# 命令行 --log-level 可覆盖
_level = INFO
_stream = sys.stdout
_file = None

# 日志走一个后台线程 + 有界队列。**这一步不是为了性能，是为了活命**：
# 如果直接在调用线程里写 stdout，而 stdout 那边没人读（管道缓冲区满了、
# journald 堵塞、磁盘写满），write 会一直阻塞；一旦还握着锁，**所有**打日志的
# 线程都会跟着卡死——请求处理器每个请求都要打一行，于是整个服务像死了一样
# 不再响应任何请求。所以：只把行塞进队列，塞不进去就丢掉并计数，绝不阻塞调用方。
_queue: "queue.Queue[str | None]" = queue.Queue(maxsize=2000)
_writer = None
_writer_lock = threading.Lock()
_dropped = 0
_LOCK = threading.Lock()          # 只保护 _dropped 这类小状态，不跨 I/O

STARTED_AT = time.time()


def setup(level: int = INFO, log_file: str | None = None, stream=None) -> None:
    """设置级别与（可选的）日志文件。level 可传常量或字符串。"""
    global _level, _file, _stream
    _level = parse_level(level)
    if stream is not None:
        _stream = stream
    if log_file:
        try:
            _file = open(log_file, "a", encoding="utf-8")
        except Exception as exc:            # 写不了文件不该拖垮服务
            print("[Log] 无法打开日志文件 " + str(log_file) + "：" + str(exc), file=sys.stderr)


def parse_level(value) -> int:
    if isinstance(value, int):
        return value
    name = str(value).strip().upper()
    for level, label in _NAMES.items():
        if label == name:
            return level
    return {"D": DEBUG, "I": INFO, "W": WARN, "E": ERROR, "WARNING": WARN}.get(name, INFO)


def level_name() -> str:
    return _NAMES.get(_level, "INFO")


def set_level(value) -> None:
    global _level
    _level = parse_level(value)


def enabled(level: int) -> bool:
    return level >= _level


def _drain() -> None:
    """后台写日志。写不动就慢慢写，绝不回头阻塞调用方。"""
    while True:
        line = _queue.get()
        if line is None:
            break
        try:
            print(line, file=_stream, flush=True)
        except Exception:
            pass
        target = _file
        if target:
            try:
                print(line, file=target, flush=True)
            except Exception:
                pass


def _ensure_writer() -> None:
    global _writer
    if _writer is not None:
        return
    with _writer_lock:
        if _writer is None:
            _writer = threading.Thread(target=_drain, name="log-writer", daemon=True)
            _writer.start()


def dropped() -> int:
    """被丢掉的日志条数（队列满时产生）。"""
    with _LOCK:
        return _dropped


def close() -> None:
    """把队列里剩下的写完再关文件。写不动就放弃，不拖住退出。"""
    global _file, _writer
    if _writer is not None:
        try:
            _queue.put_nowait(None)
            _writer.join(timeout=2.0)
        except Exception:
            pass
        _writer = None
    target, _file = _file, None
    if target:
        try:
            target.close()
        except Exception:
            pass


def _emit(level: int, message: str) -> None:
    global _dropped
    if level < _level:
        return
    now = datetime.now()
    stamp = now.strftime("%H:%M:%S") + ".%03d" % (now.microsecond // 1000)
    line = stamp + " [" + _NAMES[level] + "] " + str(message)

    _ensure_writer()
    try:
        _queue.put_nowait(line)
    except queue.Full:
        # 丢弃而不是等待：日志可以少几行，服务不能卡住
        with _LOCK:
            _dropped += 1


def log(level: int, message: str) -> None:
    _emit(level, message)


def out(message: str, level: int = INFO) -> None:
    """替换 print() 用：默认 INFO 级。"""
    _emit(level, message)


def debug(message: str) -> None:
    _emit(DEBUG, message)


def info(message: str) -> None:
    _emit(INFO, message)


def warn(message: str) -> None:
    _emit(WARN, message)


def error(message: str) -> None:
    _emit(ERROR, message)


def uptime() -> str:
    """服务已运行时长，形如 1h02m03s。"""
    seconds = int(time.time() - STARTED_AT)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return str(h) + "h" + str(m).zfill(2) + "m" + str(s).zfill(2) + "s"
    if m:
        return str(m) + "m" + str(s).zfill(2) + "s"
    return str(s) + "s"
