/*
 * pwmfan — M5Stamp-C3 4PIN ファン PWM コントローラ
 *
 * ホスト (pwmd.py) から USB シリアルで duty を受け取り、25kHz の PWM を出す。
 * ホストが落ちた・固まった場合は 30 秒で 100% 全開へフェイルセーフする。
 *
 * 回路 (esp32/wiring.png, esp32/assembly_guide.png):
 *   G5 ──[ 2N7000 ゲート ]── ドレイン ──→ ファンの PWM 線 (青)
 *        ゲートは 100kΩ で GND へプルダウン
 *   ※ FET でオープンドレイン化するため論理が反転する (ゲート High = PWM 線 Low)
 *   ※ ESP32 未起動時は FET OFF → PWM 線が 5V に張り付き 100% 全開 (安全側)
 *   ※ BSS138 レベル変換モジュールを使う場合は PWM_INVERT = false にする
 *
 * 状態 LED (オンボード SK6812 / G2):
 *   青 = boot (ホスト未接続・全開)  緑 = host 制御中  赤 = failsafe (全開)
 *
 * シリアルプロトコル (115200 8N1 / CH9102 USB-UART 経由)
 *   D <0-100>  duty を設定              -> "OK D=<n>"
 *   S          状態取得                  -> "ST duty=<n> src=<host|failsafe|boot> age=<ms>"
 *   P          疎通確認                  -> "PONG pwmfan <version>"
 *   V          ビルド情報                -> "VER pwmfan <version> pin=<n> freq=<hz>"
 * 未知のコマンドは "ERR unknown" を返す。
 *
 * ボード設定: ESP32C3 Dev Module / USB CDC On Boot: Disabled
 *   (Stamp-C3 の USB-C は CH9102 → UART0。Enabled だと Serial が未配線の native USB に向く)
 * arduino-esp32 2.x / 3.x どちらでもビルド可。
 */

#include <Arduino.h>

static const char    *VERSION     = "1.2.0";
static const uint8_t  PWM_PIN     = 5;       // G5 -> FET ゲート
static const uint8_t  PWM_CHANNEL = 0;       // 2.x のみ使用
static const uint32_t PWM_FREQ    = 25000;   // Intel 4-wire 仕様
static const uint8_t  PWM_BITS    = 10;      // 0..1023
static const uint16_t PWM_MAX     = (1 << PWM_BITS) - 1;

// 出力段の論理。配線方式に合わせてここだけ変える。
//   true  = 単石 Nch FET (2N7000 等) でオープンドレイン化した場合。
//           ゲート HIGH -> FET ON -> PWM 線 LOW なので反転が要る。
//   false = BSS138 レベル変換モジュール等、非反転バッファを挟んだ場合。
// どちらでも duty=100 が「ファン全開」を意味するよう、ここで吸収する。
// 誤るとカーブが冷却と逆に働くので、変更後は必ず D 100 で全開になることを確認する。
static const bool     PWM_INVERT  = true;

static const uint8_t  LED_PIN     = 2;       // オンボード SK6812
static const uint8_t  LED_LEVEL   = 40;      // 明るさ (0-255)

// ホストから 30 秒コマンドが来なければ全開に倒す
static const uint32_t FAILSAFE_MS = 30000;
// 起動直後もホストが喋るまでは全開で待つ
static const uint8_t  BOOT_DUTY   = 100;

static uint8_t  g_duty      = BOOT_DUTY;   // 実際に出している duty [%]
static uint8_t  g_hostDuty  = BOOT_DUTY;   // ホストが最後に指示した duty [%]
static uint32_t g_lastCmdMs = 0;
static bool     g_failsafe  = true;        // 起動直後は未受信 = フェイルセーフ状態
static bool     g_gotFirst  = false;

static String   g_line;

// ---- arduino-esp32 2.x / 3.x 差分吸収 ----
#if defined(ESP_ARDUINO_VERSION_MAJOR) && ESP_ARDUINO_VERSION_MAJOR >= 3
static void pwmInit()               { ledcAttach(PWM_PIN, PWM_FREQ, PWM_BITS); }
static void pwmWrite(uint32_t v)    { ledcWrite(PWM_PIN, v); }
static void ledWrite(uint8_t r, uint8_t g, uint8_t b) { rgbLedWrite(LED_PIN, r, g, b); }
#else
static void pwmInit() {
  ledcSetup(PWM_CHANNEL, PWM_FREQ, PWM_BITS);
  ledcAttachPin(PWM_PIN, PWM_CHANNEL);
}
static void pwmWrite(uint32_t v)    { ledcWrite(PWM_CHANNEL, v); }
static void ledWrite(uint8_t r, uint8_t g, uint8_t b) { neopixelWrite(LED_PIN, r, g, b); }
#endif

static const char *srcName() {
  if (g_failsafe) return g_gotFirst ? "failsafe" : "boot";
  return "host";
}

static void updateLed() {
  if (!g_gotFirst)     ledWrite(0, 0, LED_LEVEL);   // boot: 青
  else if (g_failsafe) ledWrite(LED_LEVEL, 0, 0);   // failsafe: 赤
  else                 ledWrite(0, LED_LEVEL, 0);   // host: 緑
}

// duty[%] を PWM へ反映する。FET で反転するので (MAX - x) を書き込む。
static void applyDuty(uint8_t duty) {
  if (duty > 100) duty = 100;
  g_duty = duty;
  uint32_t raw = (uint32_t)duty * PWM_MAX / 100;
  pwmWrite(PWM_INVERT ? (PWM_MAX - raw) : raw);
  updateLed();
}

static void handleLine(const String &raw) {
  String line = raw;
  line.trim();
  if (line.length() == 0) return;

  char cmd = toupper(line.charAt(0));
  String arg = line.length() > 1 ? line.substring(1) : String("");
  arg.trim();

  switch (cmd) {
    case 'D': {
      long v = arg.toInt();
      if (arg.length() == 0 || v < 0 || v > 100) {
        Serial.println("ERR range 0-100");
        return;
      }
      g_hostDuty  = (uint8_t)v;
      g_lastCmdMs = millis();
      g_failsafe  = false;
      g_gotFirst  = true;
      applyDuty(g_hostDuty);
      Serial.printf("OK D=%u\n", g_duty);
      break;
    }
    case 'S':
      Serial.printf("ST duty=%u src=%s age=%lu\n", g_duty, srcName(),
                    (unsigned long)(millis() - g_lastCmdMs));
      break;
    case 'P':
      Serial.printf("PONG pwmfan %s\n", VERSION);
      break;
    case 'V':
      Serial.printf("VER pwmfan %s pin=%u freq=%lu invert=%d\n", VERSION,
                    PWM_PIN, (unsigned long)PWM_FREQ, PWM_INVERT ? 1 : 0);
      break;
    default:
      Serial.println("ERR unknown");
      break;
  }
}

void setup() {
  Serial.begin(115200);
  pwmInit();
  applyDuty(BOOT_DUTY);            // ホストが喋るまでは全開 (LED 青)
  g_lastCmdMs = millis();
  g_line.reserve(32);
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (g_line.length() > 0) {
        handleLine(g_line);
        g_line = "";
      }
    } else if (g_line.length() < 31) {
      g_line += c;
    }
  }

  // ホストからの指示が途切れたら全開へ (OSフリーズ・デーモン停止対策)
  if (!g_failsafe && (millis() - g_lastCmdMs) > FAILSAFE_MS) {
    g_failsafe = true;
    applyDuty(100);                // LED 赤
    Serial.println("EV failsafe timeout -> 100%");
  }

  delay(5);
}

