"""Execute the actual APP dispatcher with substituted hardware functions."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import find_template
from test_gesture_effects import FASTLED

@unittest.skipUnless(shutil.which('g++'),'requires native C++ compiler')
class EffectDispatchTests(unittest.TestCase):
    def test_effect_only_preview_function_and_manual_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Service').mkdir();(root/'HAL').mkdir();(root/'Model').mkdir()
            (root/'Model/gesture_buffer.h').write_text('#pragma once\nextern bool currentGeneration;inline bool gestureBufferCurrent(unsigned){return currentGeneration;}\n')
            (root/'Model/ModelStore.h').write_text('#pragma once\nnamespace ModelStore{inline bool busy(){return false;}}\n')
            (root/'FastLED.h').write_text(FASTLED)
            (root/'Arduino.h').write_text('''#pragma once
#include <stdint.h>
#include <stddef.h>
using QueueHandle_t=void*;
#define portMAX_DELAY 0xffffffff
inline uint32_t millis(){return 0;}
int xQueueReceive(QueueHandle_t,void*,uint32_t);
inline QueueHandle_t xQueueCreate(int,int){return nullptr;}
inline void xTaskCreatePinnedToCore(void(*)(void*),const char*,int,void*,int,void**,int){}
''')
            (root/'HAL/HAL.h').write_text('#pragma once\nclass AnimationBase;namespace HAL{void ws2812_start_fx(AnimationBase*);}\n')
            (root/'Service/EventBus.h').write_text('''#pragma once
#include <Arduino.h>
enum EventID{EVENT_APP_SET_MODE,EVENT_GESTURE_DETECTED,EVENT_GESTURE_TRIGGER,EVENT_GESTURE_FX_PREVIEW,EVENT_BTN_SHORT_PRESS,EVENT_IMU_DATA_UPDATED,EVENT_POV_SET,EVENT_IMU_RESET_MUX,EVENT_IMU_SET_MUX};
union EventParam{int32_t i32;float f32;EventParam(int x=0):i32(x){}};
struct SystemEvent{EventID id;EventParam param1,param2;uint32_t generation=0;};
struct EventBus{static bool publish(EventID,EventParam=0,EventParam=0){return true;}static void subscribe(EventID,QueueHandle_t){}};
''')
            firmware=find_template()
            (root/'test.cpp').write_text(r'''
#include "APP/APP.cpp"
#include <cassert>
struct Done{};
bool currentGeneration=true;SystemEvent pending;bool consumed=false;int external=0,lumos=0,defaultFx=0,customFx=-99,exits=0,haAction=-1;bool mouse=false;
int xQueueReceive(QueueHandle_t,void*p,uint32_t){if(consumed)throw Done{};consumed=true;*static_cast<SystemEvent*>(p)=pending;return 1;}
namespace HAL{void ws2812_start_fx(AnimationBase*a){++defaultFx;delete a;}}
void gesture_effect_play(int id){customFx=id;}
bool ha_send(int action){haAction=action;return true;}
bool APP_ble_mouse_in_mode(){return mouse;}bool APP_ble_volume_in_mode(){return false;}bool APP_POV_in_mode(){return false;}
void APP_ble_mouse_mode_exit(){mouse=false;++exits;}void APP_ble_volume_mode_exit(){}void APP_POV_mode_exit(){}
void APP_ble_mouse_mode_enter(){mouse=true;}void APP_ble_volume_mode_enter(){}void APP_POV_mode_enter(){}
void APP_Lumos_trigger(uint32_t){++lumos;}void APP_Lumos_on(uint32_t){}void APP_Lumos_off(){}
void APP_espnow_tx_signal(uint8_t){++external;}void APP_espnow_init(){}
void APP_ble_mouse_move(float,float){}void APP_ble_volume_knob(float){}void APP_POV_on_imu_data(float,float){}void APP_POV_set_params(uint8_t,bool){}
void run(EventID id,int action,int effect){pending={id,action,effect};consumed=false;external=lumos=defaultFx=exits=0;customFx=-99;try{app_task(nullptr);}catch(Done&){} }
int main(){
 currentGeneration=false;run(EVENT_GESTURE_TRIGGER,4,5);assert(!exits&&customFx==-99);currentGeneration=true;
 mouse=true;run(EVENT_GESTURE_TRIGGER,-1,5);assert(customFx==5&&!external&&!lumos&&!defaultFx&&!exits&&mouse);
 run(EVENT_GESTURE_FX_PREVIEW,5,0);assert(customFx==5&&!external&&!lumos&&!defaultFx&&!exits&&mouse);
 run(EVENT_GESTURE_TRIGGER,0,5);assert(external==1&&customFx==5&&!defaultFx&&exits==1&&!mouse);
 mouse=true;run(EVENT_GESTURE_DETECTED,2,0);assert(mouse&&!exits&&!defaultFx&&customFx==-99);
 run(EVENT_GESTURE_DETECTED,3,0);assert(mouse&&!exits&&!defaultFx&&customFx==-99);
 run(EVENT_GESTURE_TRIGGER,5,21);assert(lumos==1&&customFx==21);
 run(EVENT_GESTURE_TRIGGER,8,21);assert(haAction==8&&customFx==21&&!lumos&&!external);
 run(EVENT_GESTURE_DETECTED,0,0);assert(external==1&&defaultFx==1&&customFx==-99);
 run(EVENT_GESTURE_TRIGGER,0,-1);assert(external==1&&defaultFx==1&&customFx==-99);
}
''')
            exe=root/('dispatch.exe' if os.name=='nt' else 'dispatch')
            build=subprocess.run(['g++','-std=c++17','-I'+str(root),'-I'+str(firmware/'src'),str(root/'test.cpp'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr);subprocess.run([str(exe)],check=True,capture_output=True)
