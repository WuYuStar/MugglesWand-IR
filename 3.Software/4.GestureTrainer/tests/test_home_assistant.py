"""Exercise the real HA worker with deterministic Wi-Fi, MQTT and clock substitutes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_runtime_bindings import FIRMWARE, PREFERENCES


ARDUINO = r'''
#pragma once
#include <algorithm>
#include <cassert>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <functional>
#include <vector>
using std::min;
struct Queue {
 size_t count, size;
 std::deque<std::vector<char>> items;
};
using QueueHandle_t=Queue*;
inline uint32_t now=0;
inline bool ota=false;
inline std::function<void()> tick;
inline void (*task)(void*)=nullptr;
#define ESP_OK 0
#define pdTRUE 1
#define pdPASS 1
#define pdMS_TO_TICKS(t) (t)
inline uint32_t millis(){return now;}
inline QueueHandle_t xQueueCreate(size_t count,size_t size){return new Queue{count,size,{}};}
inline void vQueueDelete(QueueHandle_t q){delete q;}
inline int xQueueSend(QueueHandle_t q,const void *item,int){
 if(q->items.size()>=q->count)return 0;
 auto *bytes=static_cast<const char*>(item);
 q->items.emplace_back(bytes,bytes+q->size);
 return pdTRUE;
}
inline int xQueueReceive(QueueHandle_t q,void *item,int){
 now+=50;
 if(tick)tick();
 if(q->items.empty())return 0;
 memcpy(item,q->items.front().data(),q->size);
 q->items.pop_front();
 return pdTRUE;
}
inline int xTaskCreatePinnedToCore(void(*callback)(void*),const char*,int,void*,int,void*,int){task=callback;return pdPASS;}
inline void vTaskDelete(void*){}
class IPAddress {};
'''
WIFI = r'''
#pragma once
#include <Arduino.h>
constexpr int WL_CONNECTED=3;
inline int wifiStatus=WL_CONNECTED;
class WiFiClient {
public:
 bool open=false;
 bool connect(const char*,uint16_t,int){return open=true;}
 void stop(){open=false;}
};
inline struct {
 void persistent(bool){}
 void setAutoReconnect(bool){}
 void begin(const char*,const char*){}
 int status(){return wifiStatus;}
} WiFi;
'''
MQTT = r'''
#pragma once
#include <WiFi.h>
#include <string>
struct Publication {std::string topic, payload;bool retained;};
inline std::vector<Publication> messages;
inline bool discoveryAllowed=true;
class PubSubClient {
 WiFiClient &socket;
 bool ready=false;
public:
 explicit PubSubClient(WiFiClient &client):socket(client){}
 bool setBufferSize(int){return true;}
 void setServer(const char*,int){}
 void setSocketTimeout(int){}
 void setKeepAlive(int){}
 bool connected(){if(!socket.open)ready=false;return ready;}
 bool loop(){return connected();}
 bool connect(const char*,const char*,const char*,const char*,int,bool retained,const char *will){
  assert(socket.open && !ready && retained && std::string(will)=="offline");
  return ready=true;
 }
 bool publish(const char *topic,const char *payload,bool retained){
  assert(socket.open && ready);
  if(!discoveryAllowed && std::string(topic).find("/config")!=std::string::npos)return false;
  messages.push_back({topic,payload,retained});return true;
 }
};
'''
HARNESS = r'''
#include "Service/HomeAssistant.h"
#include <PubSubClient.h>
#include <Preferences.h>
#include <string>
#include <utility>

bool OTA_active(){return ota;}
std::string reply;
void ap_web_print(const char *message,size_t length){reply.assign(message,length);}
HaSave save(std::string input){return ha_save(input.data());}
int main(){
 ha_init();
 ha_start();
 assert(ha_state()==HaState::Disabled && !task && !ha_send(8));
 const std::string config="1\nHome 2.4G\n12345678\n192.168.1.22\n1883\nwand\nsecret";
 assert(save("1\nHome\nshort\n192.168.1.22\n1883\nwand\nsecret")==HaSave::Invalid);
 assert(save("1\nHome\n12345678\n192.168.1.22\n999999999999999999\nwand\nsecret")==HaSave::Invalid);
 assert(save(config)==HaSave::Saved);
 assert(!ha_uses_wifi()); // Saving does not change the live connection until reboot.
 failWrite=true;
 assert(save("0\n\n\n\n\n\n")==HaSave::StorageError);
 failWrite=false;
 ha_init();
 assert(ha_uses_wifi());
 ha_start();
 assert(task && !ha_send(-1));
 int step=0;
 discoveryAllowed=false;
 tick=[&]{
  switch(++step){
  case 1:
   assert(ha_state()==HaState::ConnectingMqtt && !ha_send(8));
   discoveryAllowed=true;
   now+=1000;
   break;
  case 2:
   assert(ha_state()==HaState::Ready && messages.size()==2);
   assert(messages[0].retained && messages[0].topic.find("homeassistant/event/")==0);
   assert(messages[1].retained && messages[1].payload=="online");
   assert(ha_send(8));
   break;
  case 3:
   assert(messages.size()==3 && !messages.back().retained && messages.back().payload=="{\"event_type\":\"scene_1\"}");
   assert(ha_send(9));
   wifiStatus=0;
   break;
  case 4:
   assert(ha_state()==HaState::ConnectingWifi && messages.size()==3 && !ha_send(9));
   wifiStatus=WL_CONNECTED;
   break;
  case 5:
   assert(ha_state()==HaState::Ready && messages.size()==5);
   assert(ha_send(10));
   now+=751;
   break;
  case 6:
   assert(messages.size()==5 && ha_send(-1));
   break;
  case 7:
   assert(messages.size()==6 && !messages.back().retained && messages.back().payload=="{\"event_type\":\"test\"}");
   assert(reply=="{\"type\":\"haTest\",\"ok\":true}");
   ota=true;
   break;
  default: assert(false);
  }
 };
 task(nullptr);
 assert(ha_state()==HaState::Unavailable && !ha_send(8));
}
'''


@unittest.skipUnless(shutil.which('g++'), 'Native firmware test requires g++')
class HomeAssistantTests(unittest.TestCase):
    def test_opt_in_persistence_discovery_reconnect_and_expired_events(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, content in {
                'Arduino.h': ARDUINO,
                'WiFi.h': WIFI,
                'PubSubClient.h': MQTT,
                'Preferences.h': PREFERENCES,
                'esp_mac.h': '#pragma once\n#define ESP_MAC_WIFI_STA 0\n'
                             'inline int esp_read_mac(unsigned char *mac,int){for(int i=0;i<6;++i)mac[i]=i;return 0;}',
                'main.cpp': HARNESS,
            }.items():
                (root / name).write_text(content)
            exe = root / ('ha.exe' if os.name == 'nt' else 'ha')
            build = subprocess.run([
                'g++', '-std=c++17', '-I'+str(root), '-I'+str(FIRMWARE/'src'),
                str(root/'main.cpp'), str(FIRMWARE/'src/Service/HomeAssistant.cpp'), '-o', str(exe),
            ], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(exe)], env=dict(os.environ, WAND_TEST_NVS=folder),
                                 capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
