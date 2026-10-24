#!/usr/bin/env python3
"""pwmd の温度→duty追従をグラフ化する。

入力:
  /tmp/pwm_run2.log  : "<epoch> t1,h1,p1,u1,t2,h2,p2,u2" の5秒サンプル
  journalctl -u pwmdaemon : "duty -> N%" イベント(ISO時刻)
出力: /home/build/170hx/pwmdaemon/report.png
"""
import datetime as dt
import re
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

LOG = sys.argv[1] if len(sys.argv) > 1 else "/tmp/pwm_run2.log"
OUT = sys.argv[2] if len(sys.argv) > 2 else "report.png"
# 出力経路の表記 (例: "BMC SYS_FAN4", "ESP32 serial")
TITLE = sys.argv[3] if len(sys.argv) > 3 else "BMC SYS_FAN4"

ts, g1, h1, p1, u1, g2, h2, p2, u2 = [], [], [], [], [], [], [], [], []
for line in open(LOG):
    parts = line.split()
    if len(parts) != 2:
        continue
    vals = parts[1].split(",")
    if len(vals) != 8:
        continue
    ts.append(float(parts[0]))
    for arr, v in zip((g1, h1, p1, u1, g2, h2, p2, u2), vals):
        arr.append(float(v))
if not ts:
    sys.exit("no samples in %s" % LOG)

t0 = ts[0]
x = [(t - t0) / 60.0 for t in ts]          # 経過分
tmax = [max(a, b, c, d) for a, b, c, d in zip(g1, h1, g2, h2)]
ptot = [a + b for a, b in zip(p1, p2)]

# --- duty イベント (journal) -------------------------------------------
out = subprocess.run(
    ["sudo", "-n", "journalctl", "-u", "pwmdaemon", "-o", "short-iso",
     "--no-pager", "--since", "@%d" % int(t0 - 600)],
    capture_output=True, text=True).stdout
dts, dvs = [], []
for line in out.splitlines():
    m = re.match(r"(\S+)\s.*duty -> (\d+)%", line)
    if not m:
        continue
    stamp = m.group(1)
    try:
        when = dt.datetime.fromisoformat(stamp).timestamp()
    except ValueError:
        continue
    dts.append((when - t0) / 60.0)
    dvs.append(int(m.group(2)))
# サンプル区間の手前の最後のイベントを開始値として残す
while len(dts) > 1 and dts[1] <= 0:
    dts.pop(0); dvs.pop(0)
if dts:
    dts[0] = min(dts[0], 0.0)
    dts.append(x[-1])
    dvs.append(dvs[-1])

fig, axes = plt.subplots(3, 1, figsize=(11, 10), sharex=True,
                         gridspec_kw={"height_ratios": [3, 2, 2]})

ax = axes[0]
ax.plot(x, h1, label="170HX #1 HBM", color="#d62728", lw=1.8)
ax.plot(x, g1, label="170HX #1 GPU", color="#d62728", lw=1.2, ls="--", alpha=.7)
ax.plot(x, h2, label="170HX #2 HBM", color="#1f77b4", lw=1.8)
ax.plot(x, g2, label="170HX #2 GPU", color="#1f77b4", lw=1.2, ls="--", alpha=.7)
ax.plot(x, tmax, label="control input = MAX", color="k", lw=2.4, alpha=.85)
for y, lab in ((70, "curve 70C->65%"), (60, "curve 60C->45%"),
               (50, "curve 50C->30%")):
    ax.axhline(y, color="gray", lw=.6, ls=":")
    ax.text(x[-1], y + .4, lab, fontsize=7, ha="right", color="gray")
ax.set_ylabel("temperature [C]")
ax.set_title("pwmd: CMP 170HX temperature -> fan PWM duty (vLLM load test, %s)" % TITLE)
ax.legend(fontsize=8, ncol=3, loc="lower right")
ax.grid(alpha=.3)

ax = axes[1]
if dts:
    ax.step(dts, dvs, where="post", color="#2ca02c", lw=2.2,
            label="applied duty (pwmd log)")
    for xx, yy in zip(dts[:-1], dvs[:-1]):
        if 0 <= xx <= x[-1]:
            ax.annotate("%d%%" % yy, (xx, yy), textcoords="offset points",
                        xytext=(3, 4), fontsize=7, color="#2ca02c")
ax.set_ylabel("PWM duty [%]")
ax.set_ylim(0, 100)
ax2 = ax.twinx()
ax2.plot(x, [d * 70 for d in [dvs[0]] * len(x)], alpha=0)  # 目盛り合わせ用
ax2.set_ylim(0, 7000)
ax2.set_ylabel("BFB1012EH rpm (est. 70rpm/%)", fontsize=8)
ax.legend(fontsize=8, loc="upper left")
ax.grid(alpha=.3)

ax = axes[2]
ax.plot(x, ptot, color="#ff7f0e", lw=1.6, label="170HX x2 power [W]")
ax.set_ylabel("power [W]")
ax.set_xlabel("elapsed [min]")
ax.legend(fontsize=8, loc="upper left")
ax.grid(alpha=.3)
axu = ax.twinx()
axu.plot(x, u1, color="#9467bd", lw=.9, alpha=.6, label="util #1 [%]")
axu.set_ylim(0, 105)
axu.set_ylabel("GPU util [%]", fontsize=8)

fig.tight_layout()
fig.savefig(OUT, dpi=130)
print("wrote", OUT, "samples=%d duty_events=%d" % (len(x), len(dvs)))
print("temp max=%.0fC  duty max=%d%%  power peak=%.0fW"
      % (max(tmax), max(dvs) if dvs else -1, max(ptot)))
