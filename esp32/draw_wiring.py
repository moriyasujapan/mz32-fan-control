#!/usr/bin/env python3
"""ESP32-C3 ブロアファン制御の配線図を生成する。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

matplotlib.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

fig, ax = plt.subplots(figsize=(14.5, 10.2))
ax.set_xlim(0, 145); ax.set_ylim(0, 102); ax.axis("off")

C_PWM, C_TACH, C_12V, C_GND = "#1565c0", "#2e7d32", "#c62828", "#37474f"


def box(x, y, w, h, label, sub="", fc="#f7f9fc", ec="#37474f"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.6",
                                fc=fc, ec=ec, lw=1.8))
    ax.text(x + w / 2, y + h - 3.0, label, ha="center", va="top",
            fontsize=12, fontweight="bold")
    if sub:
        ax.text(x + w / 2, y + h - 7.8, sub, ha="center", va="top",
                fontsize=9, color="#455a64", linespacing=1.6)


def wire(pts, color, lw=2.4, ls="-"):
    ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, lw=lw,
            ls=ls, solid_capstyle="round", zorder=2)


def lab(x, y, text, color, fs=9, ha="center"):
    ax.text(x, y, text, color=color, fontsize=fs, ha=ha, fontweight="bold",
            bbox=dict(fc="white", ec="none", pad=1.2), zorder=4)


def dot(x, y, color):
    ax.plot([x], [y], "o", color=color, ms=5.5, zorder=5)


ax.text(72, 98.5, "ESP32-C3 ブロアファン制御 配線図   BFB1012EH (12V 2.94A / 7000rpm)",
        ha="center", fontsize=15.5, fontweight="bold")

# ---- ブロック ----------------------------------------------------------
box(3, 74, 26, 19, "ホスト PC",
    "nvidia-smi で 170HX の\nmax(GPU, HBM) を\n5 秒ごとに送信", fc="#e8eef9")
box(38, 50, 36, 43, "ESP32-C3 SuperMini",
    "受信温度 → カーブ → duty\n25kHz PWM 出力 (LEDC)\n30 秒無通信 → 100% 全開", fc="#e6f5ec")
box(84, 76, 22, 14, "BSS138 / 2N7000", "オープンドレイン化", fc="#fdf3e3")
box(112, 44, 30, 46, "BFB1012EH\n4PIN ブロア", fc="#fdebee")
box(3, 24, 30, 17, "SATA 電源 (PSU)", "ファンの電力はここから\n2.94A ≒ 35W", fc="#fdeaea")

# ---- ESP32 ピン --------------------------------------------------------
for py, name, col in ((68, "5V / USB-C", C_12V), (62, "GPIO5  PWM 出力", C_PWM),
                      (57, "GPIO4  タコ入力", C_TACH), (52.5, "GND", C_GND)):
    ax.text(40.5, py, "● " + name, fontsize=9.5, color=col, va="center")

# ---- ファンピン --------------------------------------------------------
for py, name, col in ((80, "4  PWM   (青)", C_PWM), (70, "1  GND   (黒)", C_GND),
                      (64, "2  +12V  (赤)", C_12V), (58, "3  Tach   (黄/緑)", C_TACH)):
    ax.plot([112], [py], "o", color=col, ms=7, zorder=5)
    ax.text(114.5, py, name, fontsize=10, color=col, va="center")

# ---- 配線 --------------------------------------------------------------
wire([(29, 84), (38, 84)], "#546e7a", lw=3.2)
lab(33.5, 86.4, "USB-C\n給電 + シリアル", "#546e7a", fs=8.5)

# PWM: ESP32 GPIO5 → FET ゲート → ドレイン → ファン pin4
wire([(74, 62), (79, 62), (79, 83), (84, 83)], C_PWM)
lab(79, 72, "PWM\n(論理反転)", C_PWM, fs=8.5)
wire([(106, 83), (109, 83), (109, 80), (112, 80)], C_PWM)
ax.text(95, 73.5, "ゲートは 100kΩ で GND へプルダウン\n→ 未起動時は FET OFF = 100% 全開",
        fontsize=8, color="#6d4c41", ha="center", va="top")

# タコ: ファン pin3 → プルアップ → GPIO4
wire([(112, 58), (74, 58)], C_TACH)
lab(93, 59.8, "タコ信号 (2 パルス/回転)", C_TACH, fs=8.5)
ax.plot([100, 100], [58, 50], color=C_TACH, lw=2.4)
ax.add_patch(Rectangle((98.6, 44), 2.8, 6, fc="white", ec=C_TACH, lw=1.8))
ax.text(103, 47, "10kΩ", fontsize=9, color=C_TACH, va="center")
ax.plot([100, 100], [44, 40], color=C_TACH, lw=2.4)
ax.text(100, 38.2, "3.3V", fontsize=9.5, color=C_TACH, ha="center",
        fontweight="bold")
dot(100, 58, C_TACH)

# 12V と GND のバス
wire([(33, 32), (118, 32), (118, 64), (112, 64)], C_12V)
lab(68, 33.8, "+12V (黄 → ファン赤)", C_12V)
wire([(33, 27), (126, 27), (126, 70), (112, 70)], C_GND)
lab(62, 25.2, "GND (黒)", C_GND)

# ESP32 GND と FET ソースを GND バスへ
wire([(56, 50), (56, 27)], C_GND, lw=1.8, ls="--")
dot(56, 27, C_GND)
ax.text(57.5, 40, "共通GND必須\n(USB給電なら自動で共通)", fontsize=8.5,
        color=C_GND, va="center")
wire([(90, 76), (90, 27)], C_GND, lw=1.8, ls="--")
dot(90, 27, C_GND)
ax.text(91.5, 30.5, "FET ソース", fontsize=8, color=C_GND)

# ---- 注意書き ----------------------------------------------------------
note = ("重要な設計ポイント\n"
        "① PWM はファン内部で 5V へプルアップされている → ESP32 に直結すると 3.6V 超で破損。必ず FET でオープンドレイン駆動する\n"
        "② FET を挟むと論理が反転する (ゲート High = 線を GND へ引く = duty 低) → ファーム側で 100-duty を出力して辻褄を合わせる\n"
        "③ ゲートを 100kΩ で GND へプルダウン → ESP32 が未起動/リセット中は FET OFF = PWM 線が 5V = 100% 全開 (安全側に倒れる)\n"
        "④ タコはオープンコレクタ出力。10kΩ で 3.3V へプルアップし、立下りエッジ割り込みで計数する (RPM = パルス毎秒 × 30)\n"
        "⑤ ホスト停止時は SATA の 12V も切れてファンごと止まるため、静音フォールバック用の温度センサー (NTC) は不要\n"
        "⑥ GPIO2/8/9 はストラッピングピン。プルダウンするとブート失敗するため PWM/タコには使わない (GPIO5/4 を使用)\n"
        "⑦ 線が交差していても黒丸(●)が無い箇所は接続しない")
ax.add_patch(FancyBboxPatch((3, 1), 139, 22, boxstyle="round,pad=0.8",
                            fc="#fffdf4", ec="#cfc9b0", lw=1.3))
ax.text(6, 20.6, note, fontsize=9.5, va="top", linespacing=1.85)

fig.tight_layout()
fig.savefig("wiring.png", dpi=135)
print("wrote wiring.png")
