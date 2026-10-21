#!/usr/bin/env python3
"""pwmd — 170HX温度連動ファン制御デーモン (GIGABYTE MZ32-AR0 / MegaRAC BMC).

仕組み:
  BMC Web API の Fan Profile 機構を閉ループとして使う。
    - プロファイル "pwmd" を作成/更新:
        policy A (CPU系): 純正 default ポリシー相当を維持 (選んだSYSFANセンサー除外)
        policy B (GPU用): 対象SYSFANセンサーを固定 duty (arrRef=[0] → 常時そのduty)
    - PUT collection/<name> はライブ適用される (モード再実行不要)
    - 起動時に strMode=pwmd で実行、終了時に strMode=default で純正へ復帰

制御入力: nvidia-smi が読む CMP 170HX 全枚の max(GPU温度, HBM温度)。
温度→duty% は設定のカーブ (線形補間)。

CLI:
  pwmd.py run      # メインループ (systemd から)
  pwmd.py status   # 現在温度・duty・RPM 表示
  pwmd.py set N    # 手動で duty N% にして維持 (デバッグ/音確認)
  pwmd.py sweep    # SYS_FAN1-4 を順に 100%↔45% にしてヘッダー位置を耳で特定
  pwmd.py restore  # 純正モードに戻し pwmd プロファイルを削除
"""
import argparse
import configparser
import json
import logging
import os
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("pwmd")

STOCK_CPU_POLICY = {  # BMC 純正 "default" プロファイルのポリシー写し
    "arrRef": [43, 30, 20, 7],
    "arrDuty": [30, 75, 90, 100],
    "arrSensor": [12],
    "arrFanSensor": [184, 186, 187, 188, 189],
    "iSensorCode": 1,
    "iPolicyType": 2,
    "iInSDR": 1,
    "iInitDuty": 30,
    "iAmbientSensor": 0,
    "iAmbientSensorTemp": 0,
    "iCpuTdp": 0,
    "iHysteresis": 0,
    "iPCIEDeviceEnable": 0,
    "arrHexVendorID": [],
    "arrHexDeviceID": [],
}
ALL_MANAGED_SENSORS = [184, 186, 187, 188, 189]
FAN_SENSOR_NAMES = {184: "CPU0_FAN", 186: "SYS_FAN1", 187: "SYS_FAN2",
                    188: "SYS_FAN3", 189: "SYS_FAN4", 190: "SYS_FAN5"}


def sd_notify(state):
    """systemd へ通知 (依存ライブラリなし)。NOTIFY_SOCKET が無ければ何もしない。

    内部ウォッチドッグはPythonスレッドが動いている前提なので、プロセスごと
    凍結した場合 (SIGSTOP・GIL固着・カーネル側ブロック) は効かない。
    systemd の WatchdogSec と組み合わせて二重化する。
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    import socket
    if addr[0] == "@":
        addr = "\0" + addr[1:]
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.connect(addr)
        s.sendall(state.encode())
        s.close()
    except Exception:
        pass


class BMCError(RuntimeError):
    pass


class BmcClient:
    """MegaRAC Web API クライアント (セッションCookie + X-CSRFTOKEN)."""

    def __init__(self, host, user, password, verify_tls=False):
        self.host = host
        self.user = user
        self.password = password
        self.base = "https://%s" % host
        self.ctx = ssl.create_default_context()
        if not verify_tls:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        self.cookie = None
        self.csrf = None
        # 再ログイン時に _request が自分自身を再帰呼び出しするため RLock 必須。
        # Lock だと 401 → re-login の経路で自己デッドロックし、プロセスは
        # active のまま制御ループが永久停止する (2026-10-20 に実際に発生)。
        self._lock = threading.RLock()

    def login(self):
        body = urllib.parse.urlencode(
            {"username": self.user, "password": self.password}).encode()
        req = urllib.request.Request(
            self.base + "/api/session", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = urllib.request.urlopen(req, context=self.ctx, timeout=15)
        data = json.loads(resp.read().decode())
        if data.get("ok") not in (0, None) or "CSRFToken" not in data:
            raise BMCError("login failed: %s" % data)
        sc = resp.headers.get("Set-Cookie", "")
        self.cookie = sc.split(";")[0] if sc else None
        self.csrf = data["CSRFToken"]
        log.debug("bmc login ok (priv=%s)", data.get("privilege"))

    def logout(self):
        """BMCのWebセッションは同時保持数に上限があるのでexit前に閉める。"""
        if self.csrf:
            try:
                self._request("DELETE", "/api/session")
            except Exception:
                pass
        self.cookie = None
        self.csrf = None

    def _request(self, method, path, payload=None, _retry=True):
        with self._lock:
            if self.csrf is None:
                self.login()
            headers = {}
            if self.cookie:
                headers["Cookie"] = self.cookie
            if self.csrf:
                headers["X-CSRFTOKEN"] = self.csrf
            data = None
            if payload is not None:
                data = json.dumps(payload).encode()
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(self.base + path, data=data,
                                         method=method, headers=headers)
            try:
                resp = urllib.request.urlopen(req, context=self.ctx, timeout=15)
                raw = resp.read().decode()
                if not raw.strip():
                    return {}
                out = json.loads(raw)
                if isinstance(out, dict) and out.get("error"):
                    raise BMCError("%s %s -> %s" % (method, path, out["error"]))
                return out
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                auth_fail = (e.code in (401, 403) or '"cc": 7' in body
                             or "Invalid Authentication" in body)
                if auth_fail and _retry:
                    log.info("session expired, re-login")
                    self.csrf = None
                    self.login()
                    return self._request(method, path, payload, _retry=False)
                raise BMCError("%s %s -> HTTP %d %s"
                               % (method, path, e.code, body[:200])) from e

    # --- Fan profile API -------------------------------------------------
    def get_mode(self):
        return self._request("GET", "/api/settings/fanprofile/mode").get("strMode")

    def set_mode(self, name):
        self._request("POST", "/api/settings/fanprofile/mode", {"strMode": name})

    def list_profiles(self):
        return [p["strName"] for p in
                self._request("GET", "/api/settings/fanprofile/collection")]

    def put_profile(self, profile):
        name = profile["strName"]
        try:
            self._request("PUT", "/api/settings/fanprofile/collection/" + name,
                          profile)
        except BMCError as e:
            m = str(e).lower()
            if "404" in m or "not found" in m or "no longer exist" in m:
                self._request("POST", "/api/settings/fanprofile/collection",
                              profile)
            else:
                raise

    def delete_profile(self, name):
        try:
            self._request("DELETE", "/api/settings/fanprofile/collection/" + name)
        except BMCError as e:
            if "404" not in str(e) and "not found" not in str(e).lower():
                raise

    def fan_rpm(self):
        req = urllib.request.Request(
            self.base + "/redfish/v1/Chassis/Self/Thermal",
            headers={"Accept": "application/json"})
        import base64
        req.add_header("Authorization", "Basic " + base64.b64encode(
            ("%s:%s" % (self.user, self.password)).encode()).decode())
        resp = urllib.request.urlopen(req, context=self.ctx, timeout=15)
        d = json.loads(resp.read().decode())
        return {f["Name"]: f.get("Reading") for f in d.get("Fans", [])}


def build_profile(duty, blower_sensor, profile_name):
    duty = int(max(1, min(100, round(duty))))
    cpu = dict(STOCK_CPU_POLICY)
    cpu["arrFanSensor"] = [s for s in ALL_MANAGED_SENSORS
                           if s != blower_sensor]
    gpu_policy = {
        "arrRef": [0],            # 常時このduty (BMC側センサ値を問わず)
        "arrDuty": [duty],
        "arrSensor": [8],         # 発火用ダミー (MB_TEMP1)
        "arrFanSensor": [blower_sensor],
        "iSensorCode": 1,
        "iPolicyType": 2,
        "iInSDR": 1,
        "iInitDuty": duty,        # BMC再起動時も同じdutyで起動
        "iAmbientSensor": 0,
        "iAmbientSensorTemp": 0,
        "iCpuTdp": 0,
        "iHysteresis": 0,
        "iPCIEDeviceEnable": 0,
        "arrHexVendorID": [],
        "arrHexDeviceID": [],
    }
    return {"strVersion": "1.00", "strName": profile_name,
            "arrPolicy": [cpu, gpu_policy]}


# --- 温度取得 -------------------------------------------------------------

def gpu_temps(pci_ids):
    """-> {busid: (gpu_temp or None, hbm_temp or None)}"""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,pci.bus_id,temperature.gpu,temperature.memory",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=20).stdout
    res = {}
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        busid = parts[1].lower()
        for want in pci_ids:
            if busid.endswith(want.lower()):
                def num(x):
                    try:
                        return int(x)
                    except ValueError:
                        return None
                res[want] = (num(parts[2]), num(parts[3]))
    return res


def max_temp(pci_ids, include_hbm=True):
    temps = gpu_temps(pci_ids)
    vals = []
    for k, v in temps.items():
        if v[0] is not None:
            vals.append(v[0])
        if include_hbm and len(v) > 1 and v[1] is not None:
            vals.append(v[1])
    return (max(vals), temps) if vals else (None, temps)


# --- カーブ -----------------------------------------------------------------

def load_curve(cfg):
    pts = []
    for row in cfg.get("curve", "table").split(";"):
        row = row.strip()
        if not row:
            continue
        t, d = row.split(":")
        pts.append((float(t), float(d)))
    pts.sort()
    if len(pts) < 2:
        raise ValueError("curve table needs >= 2 points")
    return pts


def curve_duty(pts, temp):
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for (t0, d0), (t1, d1) in zip(pts, pts[1:]):
        if t0 <= temp <= t1:
            if t1 == t0:
                return d1
            return d0 + (d1 - d0) * (temp - t0) / (t1 - t0)
    return pts[-1][1]


# --- 出力バックエンド ---------------------------------------------------------

class FanBackend:
    """duty% をファンへ届ける経路の共通インターフェース。"""

    name = "base"

    def set_duty(self, duty):
        raise NotImplementedError

    def heartbeat(self, duty):
        """毎サイクル呼ばれる。ハード側のフェイルセーフを生かすため。"""

    def status(self):
        return {}

    def stop(self, restore):
        pass


class BmcBackend(FanBackend):
    """BMC Web API の Fan Profile を書き換える (MZ32-AR0 の唯一の経路)。"""

    name = "bmc"

    def __init__(self, cfg):
        self.bmc = BmcClient(cfg.get("bmc", "host"), cfg.get("bmc", "user"),
                             cfg.get("bmc", "password"),
                             cfg.getboolean("bmc", "verify_tls", fallback=False))
        self.sensor = cfg.getint("fan", "sensor")
        self.profile = cfg.get("daemon", "profile_name", fallback="pwmd")

    def target(self):
        return FAN_SENSOR_NAMES.get(self.sensor, self.sensor)

    def set_duty(self, duty):
        self.bmc.put_profile(build_profile(duty, self.sensor, self.profile))
        if self.bmc.get_mode() != self.profile:
            self.bmc.set_mode(self.profile)

    def status(self):
        return {"mode": self.bmc.get_mode(),
                "profiles": self.bmc.list_profiles(),
                "rpm": self.bmc.fan_rpm()}

    def stop(self, restore):
        if restore:
            try:
                if self.bmc.get_mode() == self.profile:
                    self.bmc.set_mode("default")
                    log.info("restored strMode=default")
            except Exception as e:
                log.warning("restore on exit failed: %s", e)
        self.bmc.logout()


# ファームの応答行の接頭辞。これ以外は読み飛ばす。
REPLY_PREFIXES = ("OK ", "ST ", "PONG ", "VER ", "ERR ")
# 1 コマンドあたり何行まで読んで応答を探すか (ブートログ 9 行 + 余裕)
MAX_REPLY_LINES = 24


class SerialBackend(FanBackend):
    """M5Stamp-C3 (pwmfan.ino) に USB シリアルで duty を送る。

    BMC を一切経由しないので、BMCファームの不具合・セッション切れ・
    Web API の仕様変更から完全に独立する。マイコン側は 30 秒コマンドが
    途切れると自力で 100% へ倒れるため、ホストがフリーズしても冷却が続く。
    """

    name = "serial"

    def __init__(self, cfg):
        import serial  # pyserial (apt: python3-serial)
        self.port = cfg.get("serial", "port", fallback="/dev/ttyACM0")
        self.baud = cfg.getint("serial", "baud", fallback=115200)
        self.timeout = cfg.getfloat("serial", "timeout", fallback=2.0)
        # 配線が非反転 (FET のオープンドレインを経由していない) 場合に true。
        # ファーム側は duty をそのまま "明るさ" として扱うので、ホストで
        # 100-duty を送って辻褄を合わせる。
        # ※ これはファームの failsafe までは直せない。無通信 30 秒でファームは
        #    duty=100 に倒すが、非反転配線ではそれがファン 0% を意味する。
        #    恒久対策はファームの反転を外すか配線を直すこと。
        self.invert = cfg.getboolean("serial", "invert", fallback=False)
        self._serial_mod = serial
        self.ser = None
        self._lock = threading.Lock()
        self.open()

    def open(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        # M5Stamp-C3 の USB-UART (CH9102) は DTR/RTS が ESP32-C3 の EN / GPIO9
        # に繋がっており、既定のまま open すると自動リセットが掛かって duty が
        # boot(100%) に戻る。open 前に両方 Low へ倒して不要なリセットを防ぐ。
        ser = self._serial_mod.Serial()
        ser.port = self.port
        ser.baudrate = self.baud
        ser.timeout = self.timeout
        ser.write_timeout = self.timeout
        ser.dtr = False
        ser.rts = False
        ser.open()
        self.ser = ser
        # DTR/RTS を落としても cdc_acm が open 時に一度上げるため、実機では
        # 結局リセットが掛かる (実測: rst:0x1 POWERON / ROM ブートログ 236 bytes)。
        # リセット自体は安全側 (boot = 100% 全開) なので許容し、ブートログを
        # 読み捨ててからファームと喋る。
        self._drain(1.5)
        try:
            log.info("serial backend: %s -> %s", self.port, self.command("V"))
            if self.invert:
                log.warning("serial invert=true: 配線が非反転のため duty を "
                            "ホスト側で反転して送る。ファームの failsafe "
                            "(無通信30秒 -> duty 100) はファン 0% を意味する点に注意")
        except Exception as e:
            log.warning("serial probe failed: %s", e)

    def _drain(self, deadline):
        """静かになるまで読み捨てる。リセット直後の ROM ブートログ対策。"""
        end = time.time() + deadline
        prev, self.ser.timeout = self.ser.timeout, 0.15
        quiet = 0.0
        try:
            while time.time() < end:
                if self.ser.read(256):
                    quiet = 0.0
                else:
                    quiet += 0.15
                    if quiet >= 0.3:        # 0.3 秒無音になったら抜ける
                        break
        finally:
            self.ser.timeout = prev
        self.ser.reset_input_buffer()

    def command(self, line, _retry=True):
        """1 行送って 1 行受け取る。応答以外の行 (EV 通知・ブートログ) は捨てる。"""
        with self._lock:
            try:
                self.ser.write((line + "\n").encode())
                self.ser.flush()
                for _ in range(MAX_REPLY_LINES):
                    resp = self.ser.readline().decode(errors="replace").strip()
                    if not resp:
                        raise IOError("no response from %s" % self.port)
                    if resp.startswith("EV "):
                        log.warning("pwmfan event: %s", resp[3:])
                        continue
                    if not resp.startswith(REPLY_PREFIXES):
                        # "ESP-ROM:..." 等。応答と取り違えると set_duty が
                        # 誤って失敗扱いになり、再オープン→再リセットを繰り返す。
                        log.debug("pwmfan noise: %r", resp)
                        continue
                    return resp
                raise IOError("no reply within %d lines" % MAX_REPLY_LINES)
            except Exception:
                if not _retry:
                    raise
        log.info("serial reopen (%s)", self.port)
        self.open()
        return self.command(line, _retry=False)

    def set_duty(self, duty):
        out = round(duty)
        if self.invert:
            out = 100 - out
        resp = self.command("D %d" % out)
        if not resp.startswith("OK"):
            raise IOError("pwmfan rejected duty: %s" % resp)

    def heartbeat(self, duty):
        # マイコン側のフェイルセーフ (30秒) を毎サイクル餌付けする
        if duty is not None:
            self.set_duty(duty)

    def status(self):
        return {"pwmfan": self.command("S"), "port": self.port,
                "invert": self.invert}

    def stop(self, restore):
        # duty はマイコンがそのまま保持する。ホストが黙れば 30 秒で 100% へ。
        try:
            self.ser.close()
        except Exception:
            pass


def make_backend(cfg):
    kind = cfg.get("fan", "backend", fallback="bmc").strip().lower()
    if kind == "bmc":
        return BmcBackend(cfg)
    if kind == "serial":
        return SerialBackend(cfg)
    raise ValueError("unknown [fan] backend=%r (bmc | serial)" % kind)


# --- デーモン -----------------------------------------------------------------

class Controller:
    def __init__(self, cfg):
        self.backend = make_backend(cfg)
        self.sensor = cfg.getint("fan", "sensor", fallback=189)
        self.pci_ids = [p.strip() for p in
                        cfg.get("gpu", "pci_ids").split(",") if p.strip()]
        self.include_hbm = cfg.getboolean("gpu", "include_hbm", fallback=True)
        self.curve = load_curve(cfg)
        self.name = cfg.get("daemon", "profile_name", fallback="pwmd")
        self.interval = cfg.getfloat("daemon", "poll_interval", fallback=5.0)
        self.min_step = cfg.getfloat("daemon", "min_duty_step", fallback=5.0)
        self.min_duty = cfg.getfloat("daemon", "min_duty", fallback=15.0)
        self.max_duty = cfg.getfloat("daemon", "max_duty", fallback=100.0)
        self.emerg_temp = cfg.getfloat("gpu", "emergency_temp", fallback=90.0)
        self.err_full_after = cfg.getint("daemon", "sensor_error_cycles_for_full",
                                         fallback=12)
        # 非対称追従 (上昇は即応、下降は粘る)。
        #   fall_rate  : 制御用の基準温度を 1 分あたり何℃まで下げてよいか
        #   fall_offset: 下降局面ではカーブをこの℃だけ高温側にずらす (デッドバンド)
        # 熱源が止まっても放熱は続いているので、GPU温度が下がった瞬間に
        # 回転を落とすと再上昇してハンチングする。下げ方向だけ鈍らせる。
        self.fall_rate = cfg.getfloat("curve", "fall_rate", fallback=12.0)
        self.fall_offset = cfg.getfloat("curve", "fall_offset", fallback=0.0)
        self.ref_temp = None     # カーブに食わせる基準温度 (実温度とは別)
        self.peak_duty = None    # 上昇局面で到達した duty (下降時の上限)
        self.applied = None
        # 既定: 終了時に純正へ戻さない (GPU高温時の減速事故を防ぐ)
        self.restore_on_exit = cfg.getboolean("daemon", "restore_on_exit",
                                              fallback=False)
        self._stop = threading.Event()
        # ウォッチドッグ: ループが回り続けていることの心拍
        self.heartbeat = time.time()
        self.watchdog_timeout = cfg.getfloat(
            "daemon", "watchdog_timeout", fallback=max(60.0, self.interval * 12))

    def track_temp(self, temp, dt):
        """実温度から「制御用の基準温度」を作る (上昇は即時、下降は律速)。

        戻り値 (基準温度, 下降局面か) 。下降局面では fall_offset を上乗せして
        カーブを引くため、ある程度下がりきるまで強めの回転を維持する。
        """
        if self.ref_temp is None or temp >= self.ref_temp:
            self.ref_temp = temp            # 上昇は遅らせない (安全優先)
            return temp, False
        max_drop = self.fall_rate * (dt / 60.0)
        self.ref_temp = max(temp, self.ref_temp - max_drop)
        return self.ref_temp, True

    def curve_target(self, temp, dt):
        ref, falling = self.track_temp(temp, dt)
        if not falling:
            self.peak_duty = curve_duty(self.curve, ref)
            return self.peak_duty
        # 下降中: オフセットで粘らせるが、上昇局面の到達 duty は超えさせない
        # (温度が下がったのに回転が上がる、という不合理を防ぐ)
        target = curve_duty(self.curve, ref + self.fall_offset)
        if self.peak_duty is not None:
            target = min(target, self.peak_duty)
        return target

    def _watchdog(self):
        """制御ループが固まったらプロセスを殺してsystemdに再起動させる。

        BMC側のハング・HTTPスタック・nvidia-smiの無応答など、例外にならずに
        ブロックする事象は「プロセスは生きているのにファンが固定のまま」という
        最悪の静かな故障になる。心拍が途切れたら即死させるのが唯一安全。
        """
        while not self._stop.wait(5.0):
            age = time.time() - self.heartbeat
            if age > self.watchdog_timeout:
                log.critical("WATCHDOG: control loop stalled %.0fs "
                             "(last applied duty=%s) -> exiting for restart",
                             age, self.applied)
                sys.stderr.flush()
                os._exit(1)

    def apply(self, duty, force=False):
        duty = max(self.min_duty, min(self.max_duty, duty))
        if not force and self.applied is not None and \
                abs(duty - self.applied) < self.min_step:
            # 変化なしでもハード側フェイルセーフを維持するため心拍は送る
            self.backend.heartbeat(self.applied)
            return False
        self.backend.set_duty(duty)
        self.applied = duty
        log.info("duty -> %d%% (%s)", round(duty), self.target_label())
        return True

    def target_label(self):
        if isinstance(self.backend, BmcBackend):
            return "sensor %s" % self.backend.target()
        return "via %s" % self.backend.name

    def stop_gracefully(self):
        """終了時の既定は「最後のdutyを維持したまま離脱」。

        純正(default)へ戻すと SYS_FAN4 は CPU温度基準の曲線に落ちるため、
        GPUが熱い状態で systemctl stop するとブロアだけ減速して危険
        (アイドルCPU=30%前後)。BMCはプロファイルを保持し続けるので、
        触らずに抜けるのが最も安全。純正に戻すのは `pwmd.py restore` 明示時のみ。
        """
        if not self.restore_on_exit:
            log.info("exiting, leaving fan at duty=%s%% (backend=%s)",
                     None if self.applied is None else round(self.applied),
                     self.backend.name)
        self.backend.stop(restore=self.restore_on_exit)

    def request_stop(self):
        self._stop.set()

    def run(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: self.request_stop())
        log.info("pwmd start: backend=%s target=%s curve=%s interval=%.1fs",
                 self.backend.name, self.target_label(), self.curve,
                 self.interval)
        if isinstance(self.backend, BmcBackend):
            try:
                mode = self.backend.bmc.get_mode()
                if mode not in ("default", self.name):
                    log.warning("BMC mode is %r (外部プロファイル実行中). 奪取します",
                                mode)
            except Exception as e:
                log.warning("initial BMC read failed: %s", e)
        threading.Thread(target=self._watchdog, daemon=True,
                         name="watchdog").start()
        sd_notify("READY=1")
        err_cycles = 0
        last_cycle = time.time() - self.interval
        while not self._stop.is_set():
            t0 = time.time()
            self.heartbeat = t0
            sd_notify("WATCHDOG=1")   # systemd側の二重ウォッチドッグ
            try:
                temp, raw = max_temp(self.pci_ids, self.include_hbm)
                if temp is None:
                    raise RuntimeError("no temperature from nvidia-smi (%s)" % raw)
                err_cycles = 0
                dt = max(0.1, t0 - last_cycle)
                last_cycle = t0
                if temp >= self.emerg_temp:
                    duty = 100.0
                    force = True
                    self.ref_temp = temp
                else:
                    duty = self.curve_target(temp, dt)
                    force = False
                log.debug("max_temp=%s ref=%.1f -> duty=%.1f", temp,
                          self.ref_temp, duty)
                self.apply(duty, force=force)
            except Exception as e:
                err_cycles += 1
                log.error("cycle error (%d/%d): %s", err_cycles,
                          self.err_full_after, e)
                if err_cycles >= self.err_full_after:
                    try:
                        self.apply(100.0, force=True)
                        log.error("sensor unavailable too long: forced 100%%")
                        err_cycles = 0
                    except Exception as e2:
                        log.error("force-full failed: %s", e2)
            self._stop.wait(max(0.5, self.interval - (time.time() - t0)))
        self.stop_gracefully()


# --- CLI -------------------------------------------------------------------

def make_controller(path):
    cfg = configparser.ConfigParser()
    if not cfg.read(path):
        sys.exit("config not found: %s" % path)
    return cfg, Controller(cfg)


def cmd_status(cfg, ctl):
    temp, raw = max_temp(ctl.pci_ids, ctl.include_hbm)
    print("GPU temps:", json.dumps(raw, ensure_ascii=False))
    print("max temp:", temp)
    print("curve duty:", curve_duty(ctl.curve, temp) if temp else "n/a")
    print("backend:", ctl.backend.name, "->", ctl.target_label())
    for k, v in ctl.backend.status().items():
        print("  %s: %s" % (k, v))
    # ipmitool は読取専用で有効 (PWM書込コマンドはこのfwに存在しない:
    # netfn 0x2e〜0x3e 全走査済み、0x3c 0x0b OEM ID読出のみ)
    try:
        out = subprocess.run(["sudo", "-n", "ipmitool", "sdr", "type", "Fan"],
                             capture_output=True, text=True, timeout=10).stdout
        if out.strip():
            print("RPM (ipmitool):")
            print(out.rstrip())
    except Exception:
        pass


def cmd_sweep(ctl, dwell):
    if not isinstance(ctl.backend, BmcBackend):
        sys.exit("sweep は backend=bmc 専用です (ヘッダー特定用)")
    bmc = ctl.backend.bmc
    print("SYS_FAN1..4 を順に 100% -> 45% にします。風切り音が変化するファンを探してください。")
    print("Ctrl-C で中断 (自動で default に戻します)")
    try:
        for s in (186, 187, 188, 189):
            for d in (100, 45):
                bmc.put_profile(build_profile(d, s, ctl.name))
                if bmc.get_mode() != ctl.name:
                    bmc.set_mode(ctl.name)
                print("  %-9s duty %3d%%" % (FAN_SENSOR_NAMES[s], d), flush=True)
                time.sleep(dwell)
    finally:
        bmc.set_mode("default")
        bmc.delete_profile(ctl.name)
        print("default に復帰しました")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="/home/build/170hx/pwmdaemon/config.ini")
    ap.add_argument("cmd", choices=["run", "status", "set", "sweep", "restore"])
    ap.add_argument("arg", nargs="?", help="duty%% for set")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg, ctl = make_controller(a.config)

    if a.cmd == "run":
        ctl.run()
        return
    try:
        if a.cmd == "status":
            cmd_status(cfg, ctl)
        elif a.cmd == "set":
            ctl.apply(float(a.arg), force=True)
            print("duty %s%% を適用しました (%s)" % (a.arg, ctl.target_label()))
        elif a.cmd == "sweep":
            cmd_sweep(ctl, float(a.arg) if a.arg else 8.0)
        elif a.cmd == "restore":
            if not isinstance(ctl.backend, BmcBackend):
                sys.exit("restore は backend=bmc 専用です")
            bmc = ctl.backend.bmc
            if bmc.get_mode() == ctl.name:
                bmc.set_mode("default")
            bmc.delete_profile(ctl.name)
            print("BMC を純正制御 (default) に戻しました")
    finally:
        # 使い捨てセッション/ポートを残さない
        ctl.backend.stop(restore=False)


if __name__ == "__main__":
    main()
