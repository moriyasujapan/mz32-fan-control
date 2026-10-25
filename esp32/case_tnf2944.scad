// pwmfan ケース: タカチ TNF29-44 (29 x 44 x 1.6 mm) ユニバーサル基板用
//
// 基板上の構成 (2026-10-24 実装):
//   - XIAO ESP32C6 をピンソケットに挿し、USB-C を基板の短辺から外へ向ける
//   - 2N7000 + 100kΩ、ハブへの線は直出し (先にコネクタ付きでも通せる切り欠き)
//
// 出力:
//   openscad -D 'PART="base"' -o case_tnf2944_base.stl case_tnf2944.scad
//   openscad -D 'PART="lid"'  -o case_tnf2944_lid.stl  case_tnf2944.scad
//   PART="assembly" で基板・XIAO のダミーを入れた組み立て図 (確認用)
//
// 印刷: PETG / 0.4mm ノズル / 0.2mm レイヤー / 壁 3 周 / サポート不要。
//   base は開口を上に、lid は天面をベッドに (どちらも出力済みの向きのまま)。
//
// 基板寸法はタカチの図面 (TNF29-44.dxf) から:
//   外形 29 x 44、四隅 R1、穴 Ø1.0 を 2.54 ピッチで 10 x 15。
//   穴の中心は長辺の縁から 3.07、短辺の縁から 2.95 (片側) と 5.49 (反対側)。
//   取付穴は無い (純正の SW-50 ケースも溝で挟む) ので、棚で受けて蓋のリブで押さえる。

PART = "assembly";   // "base" | "lid" | "assembly"

// ---- 実物に合わせて調整する寸法 -------------------------------------------
socket_h     = 8.5;  // ピンソケット本体の高さ (TNF 上面から)
hdr_spacer   = 2.5;  // XIAO 側オスピンヘッダの樹脂スペーサー厚
xiao_dx      = 0;    // XIAO 中心線の基板中心からの横ずれ (+X)。奇数列なら ±1.27
usb_overhang = 0;    // XIAO の USB 端面が基板の縁から外へ出ている量 (内側なら 0)
under        = 3.0;  // 床から基板下面まで (裏のはんだ・線の逃げ)

// ---- 基本寸法 -----------------------------------------------------------
pcb_w = 29;  pcb_l = 44;  pcb_t = 1.6;  pcb_r = 1;
clr    = 0.5;   // 基板と壁のすき間 (片側)。0.3 では印刷品で側面がつかえた (v1, 2026-10-25)
wall   = 1.8;
floor_t = 1.6;
lid_t  = 1.6;
corner_r = 2.0; // 外形の角 R

xiao_w = 17.8; xiao_l = 21.0; xiao_pcb_t = 1.0;
usbc_h = 3.2;   // USB-C レセプタクルの高さ (XIAO 上面から)
top_clear = 2.5;

// 開口
// USB 側の切り欠き。v1 (幅 15.5、底は USB-C の下 2.6mm) では XIAO の基板 (17.8 幅) が
// 縁からはみ出していると壁の上端に乗り、基板が 3mm 以上浮いた。XIAO ごと通る幅にし、
// 底を基板上面のすぐ上まで下げて、XIAO の高さ・はみ出し量によらず落とし込めるようにする。
usb_w     = 19.0;   // XIAO 17.8 + 片側 0.6
usb_floor = 1.0;    // 切り欠きの底: 基板上面からの高さ (下の壁が基板の前後を止める)
cable_w   = 5.5;  // 線の切り欠き幅
cable_gap = 3.0;  // 蓋の舌の下に残す線の通り道の高さ
led_win = [8, 7]; // 蓋の窓 (X, Y)。XIAO の LED とリセット/ブートボタンの上

// スナップ
skirt_d = 4.0;  skirt_t = 1.2;  skirt_gap = 0.15;
snap_h  = 0.5;  // 突起・溝の深さ
snap_z  = 2.2;  // 壁の上端から溝の中心まで

// ---- 導出値 (z はベース底面 = 0) ------------------------------------------
in_w = pcb_w + 2*clr;
in_l = pcb_l + 2*clr + usb_overhang;
out_w = in_w + 2*wall;
out_l = in_l + 2*wall;

board_z   = floor_t + under;          // 基板下面
board_top = board_z + pcb_t;
xiao_z    = board_top + socket_h + hdr_spacer;
xiao_top  = xiao_z + xiao_pcb_t;
base_h    = xiao_top + usbc_h + top_clear;   // ベース壁の上端 = 蓋の下面
xc        = wall + in_w/2 + xiao_dx;          // XIAO 中心 X (外形座標)
board_y0  = wall + clr + usb_overhang;        // 基板の USB 側の縁 (Y)
xiao_y0   = board_y0 - usb_overhang;          // XIAO の USB 側の端 (Y)

cable_z = board_top + 1.0;                    // 線の切り欠きの底

$fn = 40;
eps = 0.01;

module rrect(w, l, h, r) {
  hull() for (x = [r, w - r], y = [r, l - r]) translate([x, y, 0]) cylinder(r = r, h = h);
}

// 長辺の壁の内側に入れる三角断面の溝 / 蓋スカートの外側の突起
module tri_bar(len, depth, toward_minus_x = false) {
  // X 方向に depth (toward_minus_x なら -X)、Z 方向に ±depth の三角柱を Y に len だけ伸ばす
  s = toward_minus_x ? -1 : 1;
  rotate([-90, 0, 0]) linear_extrude(len)
    polygon([[0, -depth], [s * depth, 0], [0, depth]]);
}

module base() {
  difference() {
    rrect(out_w, out_l, base_h, corner_r);
    // 中空
    translate([wall, wall, floor_t]) cube([in_w, in_l, base_h]);
    // USB 側: XIAO ごと通る幅で上から U 字に切り欠き (蓋の板が上を閉じる)
    translate([xc - usb_w/2, -eps, board_top + usb_floor]) cube([usb_w, wall + 2*eps, base_h]);
    // 線: 反対側の短辺を上から切り欠き (蓋の舌が上を塞ぐ)
    translate([wall + in_w/2 - cable_w/2, out_l - wall - eps, cable_z]) cube([cable_w, wall + 2*eps, base_h]);
    // スナップ溝 (長辺 2 面の内側)
    for (side = [0, 1])
      translate([side ? wall + in_w : wall, wall + 3, base_h - snap_z])
        tri_bar(in_l - 6, snap_h + 0.1, toward_minus_x = side == 0);
  }
  // 基板を受ける棚 (長辺 2 面)。裏のパッド (縁から 2.2mm 以降) に掛からない幅
  ledge = clr + 1.2;
  for (x = [wall, wall + in_w - ledge])
    translate([x, wall, floor_t - eps]) cube([ledge, in_l, under + eps]);
}

module lid() {
  // 天面の板
  difference() {
    translate([0, 0, base_h]) rrect(out_w, out_l, lid_t, corner_r);
    // LED / ボタンの窓: XIAO の USB 側の端から 1.5mm 内側から
    translate([xc - led_win[0]/2, xiao_y0 + 1.5, base_h - eps]) cube([led_win[0], led_win[1], lid_t + 2*eps]);
  }
  // スナップのスカート (長辺 2 面)
  sx_in = wall + skirt_gap;
  for (side = [0, 1]) {
    x0 = side ? wall + in_w - skirt_gap - skirt_t : sx_in;
    translate([x0, wall + 4, base_h - skirt_d]) cube([skirt_t, in_l - 8, skirt_d + eps]);
    translate([side ? wall + in_w - skirt_gap : sx_in, wall + 4, base_h - snap_z])
      tri_bar(in_l - 8, snap_h, toward_minus_x = side == 0);
  }
  // 基板を上から押さえる四隅のリブ。長辺と短辺の両方の壁に沿う L 字にして、
  // 細い柱 (17mm 超) の印刷時の揺れと押さえ力の逃げを防ぐ。基板上面との間に 0.1。
  // USB 側は基板の縁に合わせる (usb_overhang > 0 でも基板の角を押さえる)。
  leg = clr + 1.2;   // 基板の縁に 1.2 掛かる (穴のランドは縁から 2.2 以降)
  arm = 3.5;
  rib_z = board_top + 0.1;
  for (sx = [0, 1], sy = [0, 1]) {
    x0 = sx ? wall + in_w - skirt_gap : wall + skirt_gap;
    y0 = sy ? wall + in_l - skirt_gap : board_y0 - clr + skirt_gap;
    dx = sx ? -1 : 1;  dy = sy ? -1 : 1;
    translate([x0, y0, rib_z]) {
      // 長辺側の脚
      translate([dx < 0 ? -leg : 0, dy < 0 ? -arm : 0, 0]) cube([leg, arm, base_h - rib_z + eps]);
      // 短辺側の脚
      translate([dx < 0 ? -arm : 0, dy < 0 ? -leg : 0, 0]) cube([arm, leg, base_h - rib_z + eps]);
    }
  }
  // 線の切り欠きを上から塞ぐ舌 (線の通り道 cable_gap を残す)
  tz = cable_z + cable_gap;
  translate([wall + in_w/2 - cable_w/2 + 0.2, out_l - wall, tz]) cube([cable_w - 0.4, wall, base_h - tz + eps]);
}

// 確認用ダミー
module dummy() {
  color("green") translate([wall + clr, board_y0, board_z]) rrect(pcb_w, pcb_l, pcb_t, pcb_r);
  color("black") for (dx = [-7.62, 7.62])
    translate([xc + dx - 1.27, board_y0 + 2.95, board_top]) cube([2.54, 7*2.54, socket_h]);
  color("purple") translate([xc - xiao_w/2, xiao_y0, xiao_z]) cube([xiao_w, xiao_l, xiao_pcb_t]);
  color("silver") translate([xc - 4.5, xiao_y0 - 1, xiao_top]) cube([9, 7.5, usbc_h]);
}

// 断面図 (確認用): XIAO の中心で縦に切る
module section() {
  intersection() {
    union() { color("steelblue") base(); color("orange") lid(); dummy(); }
    translate([-1, -1, -1]) cube([xc + 1 - 0.37, out_l + 2, base_h + lid_t + 2]);
  }
}

if (PART == "base") base();
else if (PART == "section") section();
else if (PART == "lid") translate([0, out_l, base_h + lid_t]) rotate([180, 0, 0]) lid();
else { base(); color("orange", 0.35) lid(); dummy(); }
