# mz32-fan-control — GIGABYTE MZ32-AR0 で GPU 温度連動ファン制御

GIGABYTE **MZ32-AR0** (AMI MegaRAC BMC) に載せた **NVIDIA CMP 170HX ×2** の
温度に追従して、4PIN ブロアファンを回すための実装一式。

**このボードは IPMI でファン制御ができません。** netfn 0x2E/0x30/0x32/0x34/
0x36/0x38/0x3A/0x3C/0x3E を全 256 コマンド走査しても PWM 書き込みコマンドは
存在せず、PCIe P2A ブリッジと LPC iLPC2AHB バックドアもファームで無効化済み。
GIGABYTE 公式サポートの回答も「**BMC Web GUI の Fan Profile を使え**」でした。
本リポジトリはその Fan Profile 機構を REST API 経由で叩く常駐デーモンと、
BMC に依存しない ESP32 版への移行設計をまとめたものです。

| ディレクトリ | 内容 |
|---|---|
| `pwmd.py` ほか | **現行版**: BMC Web API Fan Profile を叩くデーモン (稼働中・負荷試験済み) |
| [`esp32/`](esp32/) | **移行先**: ESP32-C3 から直接 PWM を出す構成の配線図・基板実装図・部品表 |

### 動作環境 (検証済み)

- GIGABYTE MZ32-AR0 **rev 1.x** / BIOS R28 / **BMC fw 12.61.39**
- NVIDIA CMP 170HX ×2 (nvidia-smi で GPU/HBM 温度取得)
- BitWindow BFB1012EH (12V 2.94A 7000rpm ブロア) + CTRICALVER 10way PWM ハブ

> **rev 3.0 + BMC fw 12.61.35 ではファン制御が壊れている報告があります**
> ([unraid forum #126905](https://forums.unraid.net/topic/126905-fan-control-for-gigabyte-mz32-ar0/)):
> ファンプロファイルに実在しない `BPB_FAN_1A〜4A` しか出ず、全ファン常時フル回転・
> RPM 読み取り不可。rev 1.x では正常動作を確認しています。

---

## 現行版 (BMC Web API)

CMP 170HX ×2 の温度(MAX)を見て、**SYS_FAN4ヘッダー**のPWM信号で
BitWindow **BFB1012EH** (12V 2.94A 7000rpm ブロア) を回すデーモン。
ファン電源は CTRICALVER 10way PWMハブのSATA給電、マザーからはPWM+GNDのみ取る。

```
[SATA→ハブ 12V] ──→ BFB1012EH 赤/黒
[MZ32 SYS_FAN4 Pin4(PWM)+Pin1(GND)] ──→ ハブPWM入力 ──→ ブロア 青(PWM)
```

- BFB1012EHは35W消費 → ヘッダー給電(1A)不可、ハブSATA給電が必須 (duty1%≈70rpm)
- **SYS_FAN5は使用不可**: ポリシー対象外でPWM常時フル固定と実測 (190=3900rpm不変)
- **タコメータはハブが戻さない構成** → BMC上 SYS_FAN4 は常に "No Reading"。
  制御はduty開ループなので支障なし(RPM確認は不可)

### 実配線後の実測 (2026-10-20)

| duty | 実機の挙動 |
|---|---|
| 100% | 轟音で全開 (PWM経路の導通確認) |
| 20%  | 静かに安定回転 |
| 15%  | まだ回る (失速せず) |

→ 失速余裕があるので `min_duty = 20` を確定採用。
アイドル(170HX 2枚 44W/41W・util 0%)で duty 34% のまま5分間平衡、
GPU 39/37℃・HBM 52/49℃ で上昇なしを確認。

### vLLM負荷試験 (2026-10-20, グラフ: `report.png`)

`flash-next-w4a16` (TP=2, 両170HX) に16並列で4分半負荷 (151req / 88,968tok /
335 tok/s / エラー0)。ロードジェネレータは `loadtest.py`、作図は `plot_report.py`。

| フェーズ | 温度MAX(HBM) | 適用duty |
|---|---|---|
| アイドル | 53℃ | 33% |
| 負荷立ち上がり | 60→65℃ | 45→49→57% |
| 全負荷平衡 (388W peak, util 96%) | **69〜70℃** | **63%** |
| 冷却 | 65→55℃ | 57→51→45→39% |

カーブどおり(70℃→65%)に追従し、オーバーシュート・ハンチングなし。
HBMが常にGPUコアより+9〜11℃高く、制御入力は実質HBM温度になる。


## 制御方式 (実機で検証済み)

MZ32-AR0 の AMI MegaRAC BMC (fw 12.61) は **IPMIでのPWM書込が存在しない**
(in-band/KCSで netfn 0x30・0x3c を全256cmdスキャン済み。0x3c 0x0b の OEM ID
読出のみ有効。Lenovo HR350A=同基板の `raw 0x3c 0x14` Set Fan Speed も
Invalid command。DCMI/Redfish PATCHも不可)。ipmitool は監視専用
(`sudo ipmitool sdr type Fan` でRPM読取可、`status` に出す)。
生きた制御経路は **BMC Web API の Fan Profile 機構**のみ:

| 操作 | API |
|---|---|
| ログイン | `POST /api/session` (form) → `QSESSIONID` + `X-CSRFTOKEN` |
| プロファイル作成 | `POST /api/settings/fanprofile/collection` |
| duty 変更 (**ライブ反映**) | `PUT /api/settings/fanprofile/collection/<name>` |
| 実行 / 純正復帰 | `POST /api/settings/fanprofile/mode` `{"strMode":"pwmd"/"default"}` |
| RPM 読取 | Redfish `GET /redfish/v1/Chassis/Self/Thermal` (Basic auth) |

実測: duty 40→900 / 60→1350 / 100→2400 RPM とリニア。モード実行中は
**プロファイル内のポリシーだけ**が有効になり、本デーモンは
- policy A: 純正 CPU ファン曲線をそのまま再現 (選んだSYSFANセンサーのみ除外)
  → CPU0_FAN や他の SYS_FAN の挙動は純正と同一に保たれる
- policy B: 対象 SYSFAN を固定 duty。デーモンが温度→duty を計算して再書き込み

する。BMC 側の温度センサーは全部 None (読めない) なので、温度入力に
`nvidia-smi` を使う host 側ループが正解。

## 結線の注意

- ファンへは **PWM信号 + GND** の2本が必須。SATA電源のGNDとBMCのGNDは
  同一 PSU 系なら共通でOK (別PSUなら必ず結合)。
- PWM入力だけ抜く分配器は GND も通っているものを使う。
- タコメーター(SYS_FANx RPM)は分配器で戻さなくてよい。BMCには "No Reading"
  のままになる(実測で制御に支障なし)。

## 障害記録: 再ログインで自己デッドロック (2026-10-20 → 10-21に発覚)

**症状**: `systemctl is-active` は active のまま、最後のduty書き込みが17時間前で固定。
その間に動画生成で170HXが **GPU 85℃ / HBM 89℃、SW Thermal Slowdown発動**。
ファンはduty 33%のまま。ログの最後は `INFO session expired, re-login` (17:41:54)。

**原因**: `BmcClient._request()` が `with self._lock:` を保持したまま、401時の
再ログイン経路で **自分自身を再帰呼び出し**していた。`threading.Lock` は再入不可の
ため、BMCセッション失効の瞬間に制御スレッドが futex で永久停止。
プロセスは生存＝systemdからは正常に見える、典型的なサイレント故障。

**対策**:
1. `self._lock = threading.RLock()` (再入可能に)
2. **ウォッチドッグ追加**: 制御ループの心拍が `watchdog_timeout` (既定60秒)
   途切れたら `log.critical` + `os._exit(1)` → systemd `Restart=always` で再起動。
   BMCハング・nvidia-smi無応答など「例外にならずブロックする」事象も拾える。
   実動試験済み (心拍停止10秒で exit 1)。

教訓: ファン制御デーモンは「落ちる」より「生きたまま固まる」方が危険なので、
プロセス生存ではなく**制御ループの心拍**を監視対象にすること。

### 自動復帰の3層構造 (いずれも実機で試験済み)

| 層 | 対象となる故障 | 動作 | 試験結果 |
|---|---|---|---|
| 1. 例外リトライ | BMC 401/タイムアウト/nvidia-smi一時失敗 | 5秒ごと再試行、401は自動再ログイン | 実障害(工場リセットで認証喪失)から自動復帰を確認 |
| 2. 内部ウォッチドッグ | 制御ループのブロック (HTTPスタック等) | 心拍60秒途切れ→`os._exit(1)`→systemd再起動 | 心拍停止10秒で exit 1 を確認 |
| 3. **systemd WatchdogSec** | **プロセス丸ごと凍結** (SIGSTOP/GIL固着/カーネルブロック) | `sd_notify(WATCHDOG=1)`断→60秒でSIGABRT→再起動 | **SIGSTOP試験: 59秒で検知→68秒後に新プロセスが制御再開** |

`StartLimitIntervalSec=0` で「再起動を諦める」systemdの既定動作も無効化済み
(冷却デーモンが再起動放棄で停止するのを防ぐ)。

終了時は既定で **BMCを触らず最後のdutyを維持**する (`restore_on_exit = false`)。
純正へ戻すと SYS_FAN4 がCPU温度基準の曲線に落ち、GPU高温時に減速してしまうため。
純正に戻したいときだけ `pwmd.py restore` を明示実行する。

## 使い方

```
cp config.example.ini config.ini    # 最初に設定を作る (BMCのIP/認証情報)
chmod 600 config.ini                # ※ config.ini は .gitignore 済み

python3 pwmd.py status              # 温度・duty・RPM・BMC状態
python3 pwmd.py set 60              # 手動60% (音/風量確認)
python3 pwmd.py sweep               # SYS_FAN1→4 を順に 100%↔45%。耳で接続Headers特定
python3 pwmd.py restore             # 純正(BMC default)へ復帰+プロファイル削除
python3 pwmd.py run                 # 常駐ループ (systemd用)
```

接続ヘッダーが不明な間は `sweep` で特定して `config.ini [fan] sensor` に反映
(186=SYS_FAN1 … 189=SYS_FAN4)。

## systemd インストール (root 必要)

```
sudo cp pwmdaemon.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pwmdaemon
journalctl -fu pwmdaemon
```

デーモンは **特権不要** (BMC へ HTTPS のみ)。停止時は自動で default に復帰。
クラッシュ時は最後の duty が保持される (min_duty 以上なので停止しない)。
BMC が再起動しても iInitDuty=現在のduty で起動する。

## 設定 (config.ini)

- `[curve] table`: `温度℃:duty%` を `;` 区切り。線形補間。
  例: `35:20; 50:30; 60:45; 70:65; 75:80; 80:92; 85:100`
- `[gpu] include_hbm = true`: HBM 温度(実測92Cまで行った実 hotspot)も MAX に合流。
- `[gpu] emergency_temp = 90`: 超過で即100%。
- `[fan] sensor`: 制御対象ヘッダーセンサー。
- 温度が読め连续12サイクル失敗→100%全開(failsafe)。

## 検証記録 (2026-10-20 実機)

- gtest プロファイルで CPU0_FAN 450↔900↔2400 RPM のライブ追従を確認
- `set`/`run`/`restore` 往復、BMC default・450RPM・profiles=[default] へ完全復旧
- 170HX アイドル 31–35°C / HBM 44–54°C → カーブどおり duty ~20–29%
