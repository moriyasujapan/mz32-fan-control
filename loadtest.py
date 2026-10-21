#!/usr/bin/env python3
"""vLLM(flash-next-w4a16)に並列負荷をかけて170HXを加熱する簡易ロードジェネレータ。

使い方: python3 loadtest.py <秒数> <並列数>
"""
import json
import sys
import threading
import time
import urllib.request

URL = "http://127.0.0.1:18024/v1/completions"
MODEL = "flash-next-w4a16"
PROMPT = ("Write a detailed technical essay about datacenter thermal "
          "management, PWM fan control loops, and GPU power delivery. ")

stop_at = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 300)
nthread = int(sys.argv[2] if len(sys.argv) > 2 else 8)
counter = {"req": 0, "tok": 0, "err": 0}
lock = threading.Lock()


def worker(idx):
    while time.time() < stop_at:
        body = json.dumps({
            "model": MODEL,
            "prompt": PROMPT * 4,
            "max_tokens": 1024,
            "temperature": 0.8,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            URL, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read().decode())
            n = d.get("usage", {}).get("completion_tokens", 0)
            with lock:
                counter["req"] += 1
                counter["tok"] += n
        except Exception:
            with lock:
                counter["err"] += 1
            time.sleep(1)


ts = [threading.Thread(target=worker, args=(i,), daemon=True)
      for i in range(nthread)]
t0 = time.time()
for t in ts:
    t.start()
for t in ts:
    t.join()
el = time.time() - t0
print("requests=%d tokens=%d errors=%d elapsed=%.0fs tok/s=%.1f"
      % (counter["req"], counter["tok"], counter["err"], el,
         counter["tok"] / max(el, 1)))
