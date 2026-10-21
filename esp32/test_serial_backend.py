#!/usr/bin/env python3
"""pwmd.py の serial バックエンドを、モックファーム (pty) 相手に検証する。

実機 ESP32 が無くても以下を確認できる:
  1. バージョン問い合わせ (V) が通る
  2. duty 指示 (D) が反映される
  3. ヒステリシス内でも heartbeat が送られ、ESP32 のフェイルセーフが維持される
  4. ホストが黙るとフェイルセーフが発火して 100% になる
  5. ポートが落ちても自動再オープンする
"""
import configparser
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mock_pwmfan  # noqa: E402
import pwmd  # noqa: E402

FAILSAFE = 3.0
fails = []
checks = []


def check(name, cond, detail=""):
    checks.append(name)
    print("  %-52s %s %s" % (name, "PASS" if cond else "FAIL", detail))
    if not cond:
        fails.append(name)


def make_cfg(port):
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "fan": {"backend": "serial", "sensor": "189"},
        "serial": {"port": port, "baud": "115200", "timeout": "1.0"},
        "gpu": {"pci_ids": "41:00.0", "include_hbm": "true",
                "emergency_temp": "90"},
        "curve": {"table": "35:20; 60:45; 70:65; 85:100"},
        "daemon": {"profile_name": "pwmd", "poll_interval": "1",
                   "min_duty_step": "5", "min_duty": "20", "max_duty": "100",
                   "sensor_error_cycles_for_full": "12",
                   "watchdog_timeout": "60", "restore_on_exit": "false"},
    })
    return cfg


def main():
    port, fw, stop, _ = mock_pwmfan.start(FAILSAFE)
    print("mock pwmfan on %s (failsafe %.1fs)\n" % (port, FAILSAFE))
    cfg = make_cfg(port)

    print("[1] バックエンド初期化とプローブ")
    be = pwmd.SerialBackend(cfg)
    check("V (バージョン応答)", "pwmfan" in be.command("V"), be.command("V"))
    check("P (疎通)", be.command("P").startswith("PONG"))
    check("起動直後は全開 (boot = 100%)", fw.duty == 100 and fw.src == "boot",
          "duty=%d src=%s" % (fw.duty, fw.src))

    print("\n[2] duty 指示")
    be.set_duty(63)
    check("D 63 が反映される", fw.duty == 63 and fw.src == "host",
          "duty=%d src=%s" % (fw.duty, fw.src))
    st = be.command("S")
    check("S が duty=63 を返す", "duty=63" in st, st)
    be.set_duty(20.4)
    check("小数は丸めて送られる (20.4 -> 20)", fw.duty == 20, "duty=%d" % fw.duty)

    print("\n[3] Controller 経由 (ヒステリシスと heartbeat)")
    ctl = pwmd.Controller.__new__(pwmd.Controller)   # GPU 非依存で組み立てる
    ctl.backend, ctl.applied = be, None
    ctl.min_duty, ctl.max_duty, ctl.min_step = 20.0, 100.0, 5.0
    changed = ctl.apply(45)
    check("初回 apply(45) は送信される", changed and fw.duty == 45,
          "duty=%d" % fw.duty)
    t_before = fw.last_cmd
    time.sleep(0.2)
    changed = ctl.apply(47)          # ステップ 5% 未満 → ログ上は変更なし
    check("ヒステリシス内は duty を据え置く", not changed and fw.duty == 45,
          "duty=%d" % fw.duty)
    check("据え置きでも heartbeat が届く (フェイルセーフ維持)",
          fw.last_cmd > t_before, "age=%.2fs" % (time.time() - fw.last_cmd))
    ctl.apply(70)
    check("ステップを超えたら送信される", fw.duty == 70, "duty=%d" % fw.duty)

    print("\n[4] フェイルセーフ (ホスト沈黙 %.0f秒)" % FAILSAFE)
    time.sleep(FAILSAFE + 0.8)
    check("無通信で 100% に倒れる", fw.duty == 100 and fw.src == "failsafe",
          "duty=%d src=%s" % (fw.duty, fw.src))
    be.set_duty(55)
    check("再開すると host 制御へ復帰", fw.duty == 55 and fw.src == "host",
          "duty=%d src=%s" % (fw.duty, fw.src))

    print("\n[5] ポート切断からの自動復旧")
    be.ser.close()                      # ケーブル抜け相当
    try:
        be.set_duty(33)
        ok = fw.duty == 33
    except Exception as e:
        ok, _ = False, print("    reopen failed:", e)
    check("切断後に再オープンして送信できる", ok, "duty=%d" % fw.duty)

    be.stop(restore=False)
    stop.set()
    print("\n結果: %d 件中 %d 件失敗" % (len(checks), len(fails)))
    if fails:
        print("失敗:", ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
