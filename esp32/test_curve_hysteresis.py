#!/usr/bin/env python3
"""非対称追従 (上昇は即応・下降は律速) の制御則をシミュレーションで検証する。

実機やBMCを一切触らずに Controller の温度→duty 変換だけを回す。
"""
import configparser
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pwmd  # noqa: E402

INTERVAL = 5.0
fails = []


def make_ctl(fall_rate, fall_offset):
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "curve": {"table": "35:20; 50:30; 60:45; 70:65; 75:80; 80:92; 85:100",
                  "fall_rate": str(fall_rate), "fall_offset": str(fall_offset)},
        "daemon": {"poll_interval": str(INTERVAL), "min_duty_step": "5",
                   "min_duty": "20", "max_duty": "100"},
    })
    ctl = pwmd.Controller.__new__(pwmd.Controller)
    ctl.curve = pwmd.load_curve(cfg)
    ctl.fall_rate = cfg.getfloat("curve", "fall_rate")
    ctl.fall_offset = cfg.getfloat("curve", "fall_offset")
    ctl.ref_temp = None
    ctl.peak_duty = None
    ctl.min_duty, ctl.max_duty = 20.0, 100.0
    ctl.min_step = 5.0
    ctl.applied = None
    return ctl


def run(ctl, temps):
    out = []
    for t in temps:
        out.append(round(ctl.curve_target(t, INTERVAL), 1))
    return out


def check(name, cond, detail=""):
    print("  %-50s %s %s" % (name, "PASS" if cond else "FAIL", detail))
    if not cond:
        fails.append(name)


print("温度プロファイル: アイドル53℃ → 負荷で78℃ → 負荷解除で急冷 → 再負荷")
temps = ([53] * 3 + [60, 68, 74, 78, 78, 78]
         + [70, 62, 56, 54] + [53] * 32
         + [66, 74, 78])

print("\n[1] 従来(対称) fall_rate=1e9 / offset=0")
sym = make_ctl(1e9, 0.0)
d_sym = run(sym, temps)
print("   ", d_sym)

print("\n[2] 非対称 fall_rate=12℃/min / offset=0℃ (既定値)")
asym = make_ctl(12.0, 0.0)
d_asym = run(asym, temps)
print("   ", d_asym)

print("\n[3] 判定")
up_sym = d_sym[:9]
up_asym = d_asym[:9]
check("上昇中は従来と同じ速さで追従する", up_sym == up_asym,
      "%s == %s" % (up_sym[-3:], up_asym[-3:]))

i_drop = 9          # 78℃ -> 70℃ に落ちた直後
check("負荷解除の直後は duty を落としすぎない",
      d_asym[i_drop] > d_sym[i_drop] + 10 and d_asym[i_drop] <= max(d_asym[:9]),
      "対称=%.0f%% 非対称=%.0f%%" % (d_sym[i_drop], d_asym[i_drop]))

check("最終的にはアイドル相当まで下がる", d_asym[-4] <= 40,
      "非対称=%.0f%% (対称は %.0f%%)" % (d_asym[-4], d_sym[-4]))

# 再上昇時に即応するか
check("再び熱くなったら即座に戻る", d_asym[-1] >= 87,
      "最終=%.0f%%" % d_asym[-1])

# 変化回数 (= BMC書き込み/ファン音の変動回数) の比較
def reversals(seq, step=5.0):
    """duty の増減が反転した回数 = 耳につくハンチングの指標。"""
    n, cur, sign = 0, seq[0], 0
    for v in seq:
        if abs(v - cur) < step:
            continue
        s2 = 1 if v > cur else -1
        if sign and s2 != sign:
            n += 1
        sign, cur = s2, v
    return n


r_sym, r_asym = reversals(d_sym), reversals(d_asym)
check("増減の反転回数が減る (ハンチング抑制)", r_asym <= r_sym,
      "対称=%d回 非対称=%d回" % (r_sym, r_asym))

print("\n結果: %d 件失敗" % len(fails))
sys.exit(1 if fails else 0)
