#!/usr/bin/env python3
"""ESP32-C3 用 最小構成基板の実装図 (入力=温度のみ / 出力=PWM+GND)。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Circle

matplotlib.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]

COLS, ROWS = 16, 12
C_GND, C_PWM, C_DRAIN = "#37474f", "#1565c0", "#ef6c00"

fig, ax = plt.subplots(figsize=(13, 8.6))
ax.set_xlim(-3.0, COLS + 5.4); ax.set_ylim(-4.4, ROWS + 3.2)
ax.set_aspect("equal"); ax.axis("off")

ax.text((COLS + 2) / 2, ROWS + 2.4,
        "最小構成 実装図   入力 = USBシリアル(温度) / 出力 = PWM + GND の 2 本だけ",
        ha="center", fontsize=14, fontweight="bold")

ax.add_patch(FancyBboxPatch((-0.9, -0.9), COLS + 0.8, ROWS + 0.8,
                            boxstyle="round,pad=0.25", fc="#e8f0d8",
                            ec="#8aa06a", lw=2))
for c in range(COLS):
    for r in range(ROWS):
        ax.add_patch(Circle((c, r), 0.17, fc="#c8d4b0", ec="#9fae86", lw=0.5,
                            zorder=1))
for c in range(0, COLS, 5):
    ax.text(c, -1.8, chr(ord("A") + c), ha="center", fontsize=8, color="#777")
for r in range(0, ROWS, 2):
    ax.text(-1.9, r, str(r + 1), va="center", fontsize=8, color="#777")


def wirep(pts, color, lw=3.0, z=3):
    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, lw=lw,
            solid_capstyle="round", zorder=z)


def pad(c, r, color):
    ax.add_patch(Circle((c, r), 0.3, fc=color, ec="white", lw=0.8, zorder=4))


# ---- ESP32 ソケット (2×8, 行間 6 ピッチ) --------------------------------
L_COL, R_COL, TOP = 3, 9, 9
left = ["5V", "GND", "3V3", "GP4", "GP3", "GP2", "GP1", "GP0"]
right = ["GP5", "GP6", "GP7", "GP8", "GP9", "GP10", "GP20", "GP21"]
ax.add_patch(FancyBboxPatch((L_COL - 0.75, TOP - 7.75), 7.5, 8.5,
                            boxstyle="round,pad=0.18", fc="#dff0e4",
                            ec="#2e7d32", lw=1.6, alpha=.6, zorder=2))
ax.text((L_COL + R_COL) / 2, TOP + 1.35, "ESP32-C3 SuperMini",
        ha="center", fontsize=11, fontweight="bold", color="#1b5e20", zorder=5)
ax.text((L_COL + R_COL) / 2, TOP - 3.3, "ピンソケットで\n抜き挿し可能に", ha="center",
        fontsize=8, color="#33691e", zorder=5, linespacing=1.5)
for i, nm in enumerate(left):
    r = TOP - i
    col = C_GND if nm == "GND" else "#b0bec5"
    pad(L_COL, r, col)
    ax.text(L_COL - 0.6, r, nm, ha="right", va="center", fontsize=8, color=col,
            fontweight="bold" if nm == "GND" else "normal", zorder=5)
for i, nm in enumerate(right):
    r = TOP - i
    col = C_PWM if nm == "GP5" else "#b0bec5"
    pad(R_COL, r, col)
    ax.text(R_COL + 0.6, r, nm, ha="left", va="center", fontsize=8, color=col,
            fontweight="bold" if nm == "GP5" else "normal", zorder=5)

# ---- GND レール (上) ---------------------------------------------------
wirep([(0, 11), (COLS - 1, 11)], C_GND, lw=4)
ax.text(0.2, 11.85, "GND レール", color=C_GND, fontsize=9.5, fontweight="bold")
wirep([(L_COL, TOP - 1), (1, TOP - 1), (1, 11)], C_GND)

# ---- FET ---------------------------------------------------------------
FC = 12
ax.add_patch(FancyBboxPatch((FC - 1.5, 5.4), 3.0, 1.4,
                            boxstyle="round,pad=0.15", fc="#cfd8dc",
                            ec="#455a64", lw=1.6, zorder=4))
ax.text(FC, 6.1, "2N7000 / BSS138", ha="center", va="center", fontsize=8.5,
        fontweight="bold", zorder=5)
for dc, nm, col in ((-1, "S", C_GND), (0, "G", C_PWM), (1, "D", C_DRAIN)):
    pad(FC + dc, 5, col)
    ax.text(FC + dc, 4.3, nm, ha="center", fontsize=9, fontweight="bold",
            color=col, zorder=5)
ax.text(FC + 0.4, 7.4, "平らな面を手前に向けて 左から S / G / D", ha="center",
        fontsize=7.5, color="#546e7a")

wirep([(R_COL, TOP), (11, TOP), (11, 5), (FC, 5)], C_PWM)   # GPIO5 → G
wirep([(FC - 1, 5), (FC - 1, 11)], C_GND)                    # S → GND レール

# 100kΩ ゲートプルダウン (G → GND レール)
wirep([(FC, 5), (FC, 8.0)], C_GND, lw=2.2)
ax.add_patch(Rectangle((FC - 0.95, 8.0), 1.9, 0.84, fc="#fff8e1",
                       ec="#8d6e63", lw=1.5, zorder=4))
ax.text(FC, 8.42, "100kΩ", ha="center", va="center", fontsize=8.5,
        fontweight="bold", zorder=5)
wirep([(FC, 8.84), (FC, 11)], C_GND, lw=2.2)

# ---- 出力 2 ピン -------------------------------------------------------
OC = 15
ax.add_patch(FancyBboxPatch((OC - 0.55, 1.45), 1.1, 2.1,
                            boxstyle="round,pad=0.15", fc="#ffe0b2",
                            ec=C_DRAIN, lw=1.5, zorder=3))
pad(OC, 3, C_PWM); pad(OC, 2, C_GND)
ax.text(OC + 0.85, 3, "PWM (青)", fontsize=9, color=C_PWM, va="center",
        fontweight="bold")
ax.text(OC + 0.85, 2, "GND (黒)", fontsize=9, color=C_GND, va="center",
        fontweight="bold")
ax.text(OC + 0.9, 4.7, "PWMハブの\nPWM入力へ", fontsize=9, color=C_DRAIN,
        fontweight="bold", linespacing=1.5)
wirep([(FC + 1, 5), (14, 5), (14, 3), (OC, 3)], C_DRAIN)   # D → PWM
wirep([(OC, 2), (OC, 11)], C_GND, lw=2.4)                  # 出力GND → レール

# ---- 注記 --------------------------------------------------------------
note = ("部品はこれだけ:  FET (2N7000 または BSS138) × 1   /   100kΩ × 1   /   2ピンヘッダ × 1\n"
        "・タコは PWM ハブがマザーへ返していない (実測: ブロア回転中も SYS_FAN4 = No Reading) ため配線しない → 10kΩ も GPIO4 も不要\n"
        "・12V はハブの SATA 給電のまま。基板には一切通さないので、細いジャンパ線で足りる\n"
        "・ESP32 が未起動/リセット中は 100kΩ で FET OFF → PWM 線が 5V に張り付き ファン 100% 全開 (安全側に倒れる)\n"
        "・GND レールを上側に置くと ESP32 の GND ピン (5V の隣) から最短で引けて、配線の交差がゼロになる")
ax.add_patch(FancyBboxPatch((-0.9, -4.3), COLS + 5.2, 3.0,
                            boxstyle="round,pad=0.35", fc="#fffdf4",
                            ec="#cfc9b0", lw=1.2))
ax.text(-0.4, -1.55, note, fontsize=9, va="top", linespacing=1.95)

fig.tight_layout()
fig.savefig("board.png", dpi=135)
print("wrote board.png")
