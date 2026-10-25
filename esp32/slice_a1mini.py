#!/usr/bin/env python3
"""ケースの 3MF を OrcaSlicer の CLI で Bambu Lab A1 mini 用にスライスする。

  python3 esp32/slice_a1mini.py esp32/case_tnf2944.3mf esp32/case_tnf2944.gcode.3mf

OrcaSlicer のシステムプロファイルは "inherits" で親をたどる作りで、CLI には
継承を解決した 1 枚の JSON を渡す必要がある。ここで機種・工程・材料の 3 つを
平坦化し、BOM どおりの印刷条件 (壁 3 周 / インフィル 25% / サポート無し) を
上書きしてからスライスする。

ORCA に OrcaSlicer の AppImage を展開したディレクトリ (squashfs-root) を指定する。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ORCA = Path(os.environ.get("ORCA", "/home/build/.local/opt/orcaslicer/squashfs-root"))
BBL = ORCA / "resources/profiles/BBL"

MACHINE = "Bambu Lab A1 mini 0.4 nozzle"
PROCESS = "0.20mm Standard @BBL A1M"
FILAMENT = os.environ.get("FILAMENT", "Generic PETG @BBL A1M")

# BOM の推奨: PETG / 0.2mm / 壁 3 周 / インフィル 25% / サポート不要
PROCESS_OVERRIDES = {
    "wall_loops": "3",
    "sparse_infill_density": "25%",
    "enable_support": "0",
    # 既定の Cool Plate では PETG が弾かれる ("Cool Plate does not support filament")。
    # A1 mini 標準付属の Textured PEI を使う。別のプレートなら PLATE で指定する。
    "curr_bed_type": os.environ.get("PLATE", "Textured PEI Plate"),
}


def index_profiles():
    by_name = {}
    for kind in ("machine", "process", "filament"):
        for p in (BBL / kind).glob("*.json"):
            try:
                d = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
            if "name" in d:
                by_name[(kind, d["name"])] = d
    return by_name


def flatten(by_name, kind, name):
    """inherits を根までたどり、子の値で上書きした 1 枚の dict を返す。"""
    chain, cur = [], name
    while cur:
        d = by_name.get((kind, cur))
        if d is None:
            raise SystemExit(f"{kind} プロファイル {cur!r} が見つからない ({BBL / kind})")
        chain.append(d)
        cur = d.get("inherits")
    flat = {}
    for d in reversed(chain):
        flat.update(d)
    flat.pop("inherits", None)
    flat["name"] = name
    flat["from"] = "system"
    return flat


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    by_name = index_profiles()
    machine = flatten(by_name, "machine", MACHINE)
    process = flatten(by_name, "process", PROCESS)
    filament = flatten(by_name, "filament", FILAMENT)
    process.update(PROCESS_OVERRIDES)

    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for key, data in (("machine", machine), ("process", process), ("filament", filament)):
            files[key] = Path(tmp) / f"{key}.json"
            files[key].write_text(json.dumps(data, ensure_ascii=False, indent=1))
        cmd = ["xvfb-run", "-a", str(ORCA / "AppRun"),
               "--slice", "0",
               "--load-settings", f"{files['machine']};{files['process']}",
               "--load-filaments", str(files["filament"]),
               "--outputdir", tmp,
               "--export-3mf", out.name,
               str(src)]
        print("$", " ".join(cmd), flush=True)
        r = subprocess.run(cmd, capture_output=True, text=True)
        tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-25:])
        produced = Path(tmp) / out.name
        if r.returncode != 0 or not produced.exists():
            raise SystemExit(f"スライス失敗 (exit {r.returncode})\n{tail}")
        out.write_bytes(produced.read_bytes())
    print(f"wrote {out} ({out.stat().st_size:,} bytes) machine={MACHINE} process={PROCESS} "
          f"filament={FILAMENT} overrides={PROCESS_OVERRIDES}")


if __name__ == "__main__":
    main()
