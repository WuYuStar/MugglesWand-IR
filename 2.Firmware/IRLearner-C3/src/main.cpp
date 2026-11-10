#include <Arduino.h>
#include <IRrecv.h>
#include <IRsend.h>
#include <IRutils.h>
#include <Preferences.h>
#include <memory>
#include <stddef.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <esp_now.h>
#include <nvs_flash.h>
#include "IrProtocol.h"

// GPIO20 is also UART0 RX. Use native USB CDC, never UART0 for this project.
constexpr uint16_t kRx = 7, kTx = 20, kCapacity = 4096;
constexpr uint32_t kMagic = 0x49524331, kProbe = 0x20DF10EF;
IRrecv receiver(kRx, kCapacity, 50, true);
decode_results result;
std::unique_ptr<IRsend> sender;
Preferences prefs;
Preferences legacyPrefs;
struct Recording {
  uint32_t magic;
  uint16_t count;
  uint16_t khz;
  uint16_t raw[kCapacity];
  uint32_t checksum;
} recording = {};
bool learning = false, probing = false, storageReady = false;
uint32_t deadline = 0, received = 0;
String command;
uint8_t activeSlot = 0;
char names[kIrSlots][kIrNameSize] = {};
uint8_t present[kIrSlots] = {};
portMUX_TYPE pendingMux = portMUX_INITIALIZER_UNLOCKED;
uint8_t pending[2 + kIrNameSize] = {}, pendingSize = 0;
uint8_t broadcast[] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
bool valid();

void onEspNow(const uint8_t *, const uint8_t *data, int length) {
  if (length < 2 || length > static_cast<int>(sizeof(pending)) ||
      data[0] < kIrList || data[0] > kIrRename || data[1] >= kIrSlots) return;
  portENTER_CRITICAL(&pendingMux);
  if (!pendingSize) { memcpy(pending, data, length); pendingSize = length; }
  portEXIT_CRITICAL(&pendingMux);
}
void report(uint8_t slot, uint8_t state) {
  IrStatePacket packet = {kIrState, slot, state, {}};
  strncpy(packet.name, names[slot], kIrNameSize - 1);
  esp_now_send(broadcast, reinterpret_cast<uint8_t *>(&packet), sizeof(packet));
  Serial.printf("IR_SLOT slot=%u state=%u name=%s\n", slot, state, packet.name);
}
bool loadSlot(uint8_t slot) {
  char key[4]; snprintf(key, sizeof(key), "s%u", slot);
  recording = {};
  if (storageReady && prefs.getBytesLength(key) == sizeof(recording))
    prefs.getBytes(key, &recording, sizeof(recording));
  if (!valid()) { recording = {}; recording.khz = 38; return false; }
  return true;
}
void handleRadio() {
  uint8_t message[sizeof(pending)], length;
  portENTER_CRITICAL(&pendingMux);
  length = pendingSize;
  if (length) { memcpy(message, pending, length); pendingSize = 0; }
  portEXIT_CRITICAL(&pendingMux);
  if (!length) return;
  const uint8_t type = message[0], slot = message[1];
  if (type == kIrList) { for (uint8_t i = 0; i < kIrSlots; ++i) { report(i, learning && activeSlot == i ? 2 : present[i] ? 1 : 0); delay(4); } return; }
  if (type == kIrLearn) {
    if (probing || learning) { report(slot, 6); return; }
    activeSlot = slot; learning = true; deadline = millis() + 60000;
    receiver.disableIRIn(); receiver.enableIRIn(); report(slot, 2); return;
  }
  if (type == kIrSend) {
    if (learning || probing || !present[slot] || !loadSlot(slot)) { report(slot, 6); return; }
    receiver.disableIRIn(); sender->sendRaw(recording.raw, recording.count, recording.khz);
    delay(100); receiver.enableIRIn(); report(slot, 4); return;
  }
  if (type == kIrDelete) {
    char key[4]; snprintf(key, sizeof(key), "s%u", slot);
    char nameKey[4]; snprintf(nameKey, sizeof(nameKey), "n%u", slot);
    if (storageReady && (!prefs.isKey(key) || prefs.remove(key))) {
      prefs.remove(nameKey); present[slot] = 0; names[slot][0] = 0;
      report(slot, 0);
    } else report(slot, 6);
    return;
  }
  if (type == kIrRename) {
    if (!present[slot] || length < 3 || message[length - 1] != 0) { report(slot, 6); return; }
    char nameKey[4]; snprintf(nameKey, sizeof(nameKey), "n%u", slot);
    if (storageReady && prefs.putString(nameKey, reinterpret_cast<char *>(message + 2)) > 0) {
      strncpy(names[slot], reinterpret_cast<char *>(message + 2), kIrNameSize - 1);
      names[slot][kIrNameSize - 1] = 0; report(slot, 1);
    } else report(slot, 6);
  }
}

uint32_t checksum(const Recording &r) {
  uint32_t h = 2166136261UL;
  const auto *bytes = reinterpret_cast<const uint8_t *>(&r);
  for (size_t i = 0; i < offsetof(Recording, checksum); ++i)
    h = (h ^ bytes[i]) * 16777619UL;
  return h;
}
bool valid() {
  return recording.magic == kMagic && recording.count > 0 &&
         recording.count <= kCapacity && recording.khz >= 20 &&
         recording.khz <= 60 && recording.checksum == checksum(recording);
}
void status() {
  Serial.printf("STATUS rx=GPIO7 tx=GPIO20 rx_level=%d received=%lu learned=%s pulses=%u carrier_khz=%u tx_configured=%s storage=%s\n",
                digitalRead(kRx), static_cast<unsigned long>(received),
                valid() ? "yes" : "no", recording.count,
                recording.khz, sender ? "yes" : "no", storageReady ? "yes" : "no");
}
void exportRecording() {
  if (!valid()) { Serial.println("ERROR no_recording"); return; }
  Serial.printf("{\"type\":\"recording\",\"rx_gpio\":7,\"tx_gpio\":20,\"carrier_khz\":%u,\"carrier_measured\":false,\"raw_us\":[", recording.khz);
  for (uint16_t i = 0; i < recording.count; ++i) {
    if (i) Serial.print(',');
    Serial.print(recording.raw[i]);
  }
  Serial.println("]}");
}
void execute(String line) {
  line.trim();
  if (line == "status") status();
  else if (line == "help")
    Serial.println("COMMANDS status | learn | cancel | dump | send | selftest | tx high | tx low | freq 38 | clear");
  else if (line == "learn") {
    if (probing) { Serial.println("ERROR selftest_busy"); return; }
    receiver.disableIRIn(); receiver.enableIRIn();
    learning = true; deadline = millis() + 60000;
    Serial.println("LEARN_READY timeout_s=60 press_remote_once");
  } else if (line == "cancel") {
    learning = probing = false; Serial.println("CANCELLED");
  } else if (line == "dump") exportRecording();
  else if (line == "tx high" || line == "tx low") {
    if (learning || probing) { Serial.println("ERROR busy"); return; }
    // Explicit configuration required: unknown modules may be active-low.
    sender.reset(new IRsend(kTx, line == "tx low"));
    sender->begin();
    Serial.printf("TX_CONFIGURED active=%s carrier_modulation=yes\n", line == "tx low" ? "low" : "high");
  } else if (line.startsWith("freq ")) {
    const String value = line.substring(5);
    bool digits = value.length() > 0;
    for (size_t i = 0; i < value.length(); ++i) digits &= isDigit(value[i]);
    const int khz = value.toInt();
    if (!digits || khz < 20 || khz > 60) { Serial.println("ERROR frequency_range_20_to_60_khz"); return; }
    recording.khz = khz;
    recording.checksum = checksum(recording);
    char key[4]; snprintf(key, sizeof(key), "s%u", activeSlot);
    if (recording.magic == kMagic && storageReady &&
        prefs.putBytes(key, &recording, sizeof(recording)) != sizeof(recording))
      Serial.println("ERROR save_frequency_failed");
    Serial.printf("FREQUENCY khz=%d measured=no\n", khz);
  } else if (line == "send" || line == "selftest") {
    if (!sender) { Serial.println("ERROR configure_tx_high_or_low_first"); return; }
    if (learning || probing) { Serial.println("ERROR busy"); return; }
    if (line == "send") {
      if (!valid()) { Serial.println("ERROR no_recording"); return; }
      receiver.disableIRIn();
      sender->sendRaw(recording.raw, recording.count, recording.khz);
      delay(100); receiver.enableIRIn();
      Serial.println("SENT verify_target_response");
    } else {
      receiver.disableIRIn(); receiver.enableIRIn();
      probing = true; deadline = millis() + 1500;
      Serial.println("SELFTEST_STARTED NEC=20DF10EF carrier_khz=38");
      Serial.flush();
      sender->sendNEC(kProbe, 32);
    }
  } else if (line == "clear") {
    learning = false;
    char key[4]; snprintf(key, sizeof(key), "s%u", activeSlot);
    if (storageReady && prefs.isKey(key) && !prefs.remove(key)) {
      Serial.println("ERROR clear_failed"); return;
    }
    char nameKey[4]; snprintf(nameKey, sizeof(nameKey), "n%u", activeSlot);
    if (storageReady) prefs.remove(nameKey);
    present[activeSlot] = 0; names[activeSlot][0] = 0;
    recording = {}; recording.khz = 38;
    Serial.println("CLEARED");
  } else Serial.println("ERROR unknown_command");
}
void setup() {
  // Leave unknown transmitter trigger polarity unconfigured until explicitly set.
  pinMode(kTx, INPUT);
  Serial.begin(115200);
  delay(1200);
  esp_err_t nvsStatus = nvs_flash_init_partition("irdata");
  if (nvsStatus == ESP_ERR_NVS_NO_FREE_PAGES || nvsStatus == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    nvs_flash_erase_partition("irdata");
    nvsStatus = nvs_flash_init_partition("irdata");
  }
  storageReady = nvsStatus == ESP_OK && prefs.begin("ir-learner", false, "irdata");
  if (storageReady && !prefs.getBool("migrated", false)) {
    if (!prefs.isKey("s0") && legacyPrefs.begin("ir-learner", true)) {
      if (legacyPrefs.getBytesLength("signal") == sizeof(recording)) {
        legacyPrefs.getBytes("signal", &recording, sizeof(recording));
        if (valid()) prefs.putBytes("s0", &recording, sizeof(recording));
      }
      legacyPrefs.end();
    }
    prefs.putBool("migrated", true);
  }
  recording = {}; recording.khz = 38;
  for (uint8_t i = 0; i < kIrSlots; ++i) {
    present[i] = loadSlot(i);
    if (present[i]) {
      char key[4]; snprintf(key, sizeof(key), "n%u", i);
      String name = prefs.getString(key, i == 0 ? "已学信号" : "红外信号");
      name.toCharArray(names[i], kIrNameSize);
    }
  }
  loadSlot(0);
  sender.reset(new IRsend(kTx, false)); sender->begin();
  WiFi.mode(WIFI_STA); WiFi.disconnect();
  esp_wifi_set_channel(1, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() == ESP_OK) {
    esp_now_register_recv_cb(onEspNow);
    esp_now_peer_info_t peer = {};
    memcpy(peer.peer_addr, broadcast, 6); peer.channel = 1;
    esp_now_add_peer(&peer);
  }
  receiver.setUnknownThreshold(12);
  receiver.enableIRIn();
  Serial.println("IR_LEARNER_READY firmware=1 rx=7 tx=20 tx_unconfigured carrier_default_khz=38 carrier_measured=no");
  status();
}
void loop() {
  handleRadio();
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\n') { execute(command); command = ""; }
    else if (c != '\r') {
      if (command.length() < 100) command += c;
      else { command = ""; Serial.println("ERROR command_too_long"); }
    }
  }
  if (receiver.decode(&result)) {
    ++received;
    const String protocol = typeToString(result.decode_type);
    Serial.printf("RX protocol=%s bits=%u value=%s entries=%u overflow=%d repeat=%d\n",
                  protocol.c_str(), result.bits, uint64ToString(result.value, 16).c_str(),
                  result.rawlen, result.overflow, result.repeat);
    if (probing && !result.overflow && result.decode_type == NEC && result.value == kProbe && result.bits == 32) {
      probing = false; Serial.println("SELFTEST_PASS transmitter_and_receiver_optical_loopback");
    }
    if (learning && !result.repeat && !result.overflow && result.rawlen > 12) {
      // Skip rawbuf[0], which is the idle gap before the frame. Count the
      // actual entries ourselves so idle time cannot add uninitialized data.
      uint32_t count = 0;
      for (uint16_t i = 1; i < result.rawlen; ++i) {
        const uint32_t us = result.rawbuf[i] * kRawTick;
        count += 1 + (us ? (us - 1) / UINT16_MAX * 2 : 0);
      }
      if (count > kCapacity) Serial.println("ERROR recording_too_large_keep_learning");
      else {
          const uint16_t khz = recording.khz;
          recording = {};
          recording.magic = kMagic; recording.count = count; recording.khz = khz;
          uint16_t pos = 0;
          for (uint16_t i = 1; i < result.rawlen; ++i) {
            uint32_t us = result.rawbuf[i] * kRawTick;
            while (us > UINT16_MAX) {
              recording.raw[pos++] = UINT16_MAX;
              recording.raw[pos++] = 0;
              us -= UINT16_MAX;
            }
            recording.raw[pos++] = us;
          }
          recording.checksum = checksum(recording);
          learning = false;
          char key[4]; snprintf(key, sizeof(key), "s%u", activeSlot);
          const bool saved = storageReady && prefs.putBytes(key, &recording, sizeof(recording)) == sizeof(recording);
          if (saved) {
            present[activeSlot] = 1;
            if (!names[activeSlot][0]) {
              snprintf(names[activeSlot], kIrNameSize, "红外信号 %u", activeSlot + 1);
              char nameKey[4]; snprintf(nameKey, sizeof(nameKey), "n%u", activeSlot);
              prefs.putString(nameKey, names[activeSlot]);
            }
            report(activeSlot, 3);
          } else report(activeSlot, 6);
          Serial.printf("LEARNED pulses=%u persistent=%s carrier_khz=%u carrier_measured=no\n", count, saved ? "yes" : "no", khz);
          exportRecording();
      }
    }
    receiver.resume();
  }
  if ((learning || probing) && static_cast<int32_t>(millis() - deadline) >= 0) {
    if (learning) report(activeSlot, 5);
    Serial.println(probing ? "SELFTEST_NO_MATCH check_alignment_power_polarity" : "LEARN_TIMEOUT");
    learning = probing = false;
  }
  delay(1);
}
