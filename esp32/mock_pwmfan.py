#!/usr/bin/env python3
"""pwmfan.ino のプロトコルを pty 上で再現するモックファームウェア。

実機 ESP32 が無くても pwmd.py の serial バックエンドを検証できる。
`python3 mock_pwmfan.py [failsafe秒]` で起動すると、接続すべき疑似ポート
(/dev/pts/N) を標準出力に表示して待ち受ける。
"""
import os
import pty
import sys
import threading
import time

BOOT_DUTY = 100


class MockFirmware:
    """pwmfan.ino と同じ状態機械。"""

    VERSION = "1.2.0-mock"

    def __init__(self, failsafe_s=30.0):
        self.failsafe_s = failsafe_s
        self.duty = BOOT_DUTY
        self.failsafe = True        # 起動直後は未受信 = フェイルセーフ状態
        self.got_first = False
        self.last_cmd = time.time()
        self.events = []
        self.raw_queue = []         # テスト用: 応答前に割り込ませる生バイト

    def emit_raw(self, data):
        """ROM ブートログのような「応答ではない行」を流し込む (テスト用)。"""
        self.raw_queue.append(data)

    @property
    def src(self):
        """ファーム側 srcName() と同じ判定。"""
        if self.failsafe:
            return "failsafe" if self.got_first else "boot"
        return "host"

    def tick(self):
        """フェイルセーフ判定。発火したら通知行を返す。

        ファームと同じく、ホストと一度も喋っていない間 (boot) は
        既にフェイルセーフ状態なので EV は出さない。
        """
        if not self.failsafe and \
                (time.time() - self.last_cmd) > self.failsafe_s:
            self.failsafe = True
            self.duty = 100
            return "EV failsafe timeout -> 100%"
        return None

    def handle(self, line):
        line = line.strip()
        if not line:
            return None
        cmd, _, arg = line.partition(" ")
        cmd = cmd[:1].upper()
        arg = arg.strip()
        if cmd == "D":
            try:
                v = int(arg)
            except ValueError:
                return "ERR range 0-100"
            if not 0 <= v <= 100:
                return "ERR range 0-100"
            self.duty = v
            self.failsafe = False
            self.got_first = True
            self.last_cmd = time.time()
            return "OK D=%d" % self.duty
        if cmd == "S":
            age = int((time.time() - self.last_cmd) * 1000)
            return "ST duty=%d src=%s age=%d" % (self.duty, self.src, age)
        if cmd == "P":
            return "PONG pwmfan %s" % self.VERSION
        if cmd == "V":
            return "VER pwmfan %s pin=5 freq=25000 invert=1" % self.VERSION
        return "ERR unknown"


def serve(fw, master_fd, stop):
    buf = b""
    while not stop.is_set():
        while fw.raw_queue:
            os.write(master_fd, fw.raw_queue.pop(0))
        ev = fw.tick()
        if ev:
            os.write(master_fd, (ev + "\n").encode())
        try:
            r = os.read(master_fd, 256)
        except (BlockingIOError, InterruptedError):
            time.sleep(0.02)
            continue
        except OSError:
            break
        if not r:
            time.sleep(0.02)
            continue
        buf += r
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            resp = fw.handle(line.decode(errors="replace"))
            if resp:
                os.write(master_fd, (resp + "\n").encode())


def start(failsafe_s=30.0):
    """(ポート名, firmware, stopイベント, スレッド) を返す。"""
    master_fd, slave_fd = pty.openpty()
    os.set_blocking(master_fd, False)
    port = os.ttyname(slave_fd)
    fw = MockFirmware(failsafe_s)
    stop = threading.Event()
    th = threading.Thread(target=serve, args=(fw, master_fd, stop), daemon=True)
    th.start()
    return port, fw, stop, th


if __name__ == "__main__":
    fs = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    port, fw, stop, th = start(fs)
    print("mock pwmfan listening on %s (failsafe %.1fs)" % (port, fs), flush=True)
    try:
        while True:
            time.sleep(1)
            print("  duty=%d src=%s" % (fw.duty, fw.src), flush=True)
    except KeyboardInterrupt:
        stop.set()
