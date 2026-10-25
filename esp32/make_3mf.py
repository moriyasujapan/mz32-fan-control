#!/usr/bin/env python3
"""ケースの STL (ASCII, OpenSCAD 出力) を 1 枚のプレートに並べた 3MF にまとめる。

OpenSCAD から直接 3MF を書くと全部品が 1 オブジェクトに結合されるので、
部品ごとに別オブジェクトとして並べ、Bambu Studio でそのまま扱えるようにする。
STL は印刷向き (底面が z=0) で出力されている前提。

  python3 esp32/make_3mf.py esp32/case_tnf2944.3mf \
      esp32/case_tnf2944_base.stl esp32/case_tnf2944_lid.stl

既定のプレートは Bambu Lab A1 mini (180 x 180 mm) で、部品を X 方向に間隔 10 mm で
並べ、全体をプレート中央に置く。
"""
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

PLATE = (180.0, 180.0)
GAP = 10.0

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""
RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def read_ascii_stl(path):
    """頂点を共有した (vertices, triangles) を返す。"""
    text = Path(path).read_text()
    coords = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)
    if not coords or len(coords) % 3:
        raise SystemExit(f"{path}: ASCII STL の頂点が読めない")
    index, verts, tris = {}, [], []
    for i in range(0, len(coords), 3):
        tri = []
        for c in coords[i:i + 3]:
            key = tuple(round(float(v), 5) for v in c)
            if key not in index:
                index[key] = len(verts)
                verts.append(key)
            tri.append(index[key])
        if len(set(tri)) == 3:          # 退化三角形は捨てる
            tris.append(tuple(tri))
    return verts, tris


def check_manifold(name, tris):
    """各辺がちょうど 2 枚の三角形に共有されているか (穴・重なりの検出)。"""
    edges = Counter()
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted(e))] += 1
    bad = sum(1 for n in edges.values() if n != 2)
    if bad:
        raise SystemExit(f"{name}: 非多様体の辺が {bad} 本ある")


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    out, stls = sys.argv[1], sys.argv[2:]
    parts = []
    for p in stls:
        verts, tris = read_ascii_stl(p)
        check_manifold(p, tris)
        xs, ys, zs = zip(*verts)
        bbox = (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
        if abs(bbox[4]) > 1e-3:
            raise SystemExit(f"{p}: 底面が z=0 にない (印刷向きで出力すること)")
        parts.append((Path(p).stem, verts, tris, bbox))

    total_w = sum(b[1] - b[0] for *_, b in parts) + GAP * (len(parts) - 1)
    max_l = max(b[3] - b[2] for *_, b in parts)
    if total_w > PLATE[0] or max_l > PLATE[1]:
        raise SystemExit(f"プレート {PLATE} に収まらない ({total_w:.1f} x {max_l:.1f})")

    objects, items = [], []
    x = (PLATE[0] - total_w) / 2
    for oid, (name, verts, tris, b) in enumerate(parts, start=1):
        vx = "\n".join(f'     <vertex x="{v[0] - b[0]:.5f}" y="{v[1] - b[2]:.5f}" z="{v[2]:.5f}"/>' for v in verts)
        tx = "\n".join(f'     <triangle v1="{a}" v2="{b_}" v3="{c}"/>' for a, b_, c in tris)
        objects.append(f'  <object id="{oid}" name="{name}" type="model">\n   <mesh>\n    <vertices>\n{vx}\n    </vertices>\n'
                       f'    <triangles>\n{tx}\n    </triangles>\n   </mesh>\n  </object>')
        ty = (PLATE[1] - (b[3] - b[2])) / 2
        items.append(f'  <item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 {x:.3f} {ty:.3f} 0"/>')
        x += (b[1] - b[0]) + GAP

    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
             ' <metadata name="Title">pwmfan case (TNF29-44)</metadata>\n'
             ' <metadata name="Application">pwmdaemon/esp32/make_3mf.py</metadata>\n'
             ' <resources>\n' + "\n".join(objects) + '\n </resources>\n'
             ' <build>\n' + "\n".join(items) + '\n </build>\n</model>\n')

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("3D/3dmodel.model", model)
    for name, verts, tris, b in parts:
        print(f"{name}: {len(verts)} 頂点 / {len(tris)} 三角形 / "
              f"{b[1]-b[0]:.1f} x {b[3]-b[2]:.1f} x {b[5]-b[4]:.1f} mm")
    print(f"wrote {out} (プレート {PLATE[0]:.0f} x {PLATE[1]:.0f} mm に {len(parts)} 部品)")


if __name__ == "__main__":
    main()
