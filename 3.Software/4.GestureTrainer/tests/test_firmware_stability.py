"""Execute production C++ with bounded hardware/RTOS substitutes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import find_template
from test_gesture_effects import FASTLED

@unittest.skipUnless(shutil.which('g++'),'requires native C++ compiler')
class FirmwareStabilityTests(unittest.TestCase):
    def native(self,files,main,sources=()):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name,text in files.items():
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
            (root/'test.cpp').write_text(main,encoding='utf-8');exe=root/('test.exe' if os.name=='nt' else 'test')
            fw=find_template()/'src'
            sanitize=['-fsanitize=address,undefined','-fno-omit-frame-pointer'] if os.environ.get('WAND_TEST_SANITIZE')=='1' else []
            p=subprocess.run(['g++','-std=c++17','-pthread',*sanitize,'-I'+str(root),'-I'+str(fw),'-I'+str(fw/'HAL'),*(str(fw/s) for s in sources),str(root/'test.cpp'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            p=subprocess.run([str(exe)],capture_output=True,text=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr)

    def test_tcp_split_join_overflow_nul_and_clients(self):
        self.native({},r'''
#include "Service/CommandLine.h"
#include <cassert>
#include <string>
#include <vector>
int main(){
 CommandLine a,b;std::vector<std::string> commands;
 auto record=[&](char *s){commands.emplace_back(s);};
 const char first[]={'i','n','f'};a.feed(first,3,record);b.feed("back\n",5,record);
 a.feed("erence\r\nimu\nback\n",18,record);
 assert((commands==std::vector<std::string>{"back","inference","imu","back"}));
 std::string longLine(10000,'x');longLine+="back\ninfo\n";a.feed(longLine.data(),longLine.size(),record);
 assert(commands.back()=="info"&&commands.size()==5);
 const char binary[]={'s','t','o','p',0,'b','a','c','k','\n'};a.feed(binary,sizeof(binary),record);assert(commands.size()==5);
 a.feed("gesture 4",9,record);a.reset();a.feed("back\n",5,record);assert(commands.back()=="back");
 std::string exact(63,'a');exact+='\n';a.feed(exact.data(),exact.size(),record);assert(commands.back().size()==63);
}
''')

    def test_inference_owns_snapshot_and_reset_invalidates_old_window(self):
        self.native({'Arduino.h':r'''
#pragma once
#include <stdint.h>
#include <mutex>
using SemaphoreHandle_t=std::mutex*;using TaskHandle_t=void*;
constexpr auto portMAX_DELAY=0xffffffff;
inline auto xSemaphoreCreateMutex(){return new std::mutex;}
inline bool xSemaphoreTake(SemaphoreHandle_t m,unsigned){m->lock();return true;}
inline void xSemaphoreGive(SemaphoreHandle_t m){m->unlock();}
inline void xTaskNotifyGive(TaskHandle_t){}
'''},r'''
#include "Model/gesture_buffer.h"
#include <cassert>
#include <cmath>
#include <thread>
int main(){
 initGestureBuffer();assert(!gestureBufferReady());float out[200];uint32_t epoch;
 for(int i=0;i<100;++i)addSample(i,-i,i==99);
 assert(gestureBufferReady());assert(acquireBuffer(out,epoch));assert(out[0]==0&&out[198]==99&&out[199]==-99);
 auto old=epoch;
 for(int i=100;i<200;++i)addSample(i,-i,i==199);
 assert(out[0]==0&&out[198]==99);assert(gestureBufferCurrent(old));
 resetGestureBuffer();assert(!gestureBufferReady());assert(!gestureBufferCurrent(old));assert(!acquireBuffer(out,epoch));
 addSample(NAN,1,true);assert(g_gesture_buffer.total_samples==0);
 std::thread writer([]{for(int k=0;k<10000;++k)addSample(k,-k,true);});
 for(int i=0;i<10000;++i)if(acquireBuffer(out,epoch))for(int n=0;n<200;n+=2)assert(out[n]==-out[n+1]);
 writer.join();delete g_gesture_buffer.mutex;
}
''',('Model/gesture_buffer.cpp',))

    def test_old_lamp_destructor_cannot_disable_new_lamp(self):
        self.native({'Arduino.h':'#pragma once\n#include <stdint.h>\ninline uint32_t millis(){return 0;}\ninline uint16_t random16(int){return 0;}\ninline uint8_t random8(int,int){return 200;}\n',
                     'FastLED.h':FASTLED,
                     'HAL/HAL.h':'#pragma once\nclass AnimationBase;namespace HAL{void ws2812_set_overlay(AnimationBase*);void enable_AnimStatus();}\n'},r'''
#include "APP/APP_Lumos.cpp"
#include <cassert>
#include <vector>
std::vector<AnimationBase*> pending;int off=0;
namespace HAL{void ws2812_set_overlay(AnimationBase *a){pending.push_back(a);}void enable_AnimStatus(){++off;}}
int main(){
 APP_Lumos_on(0xffffff);APP_Lumos_on(0x0000ff);delete pending[0];assert(lampState.load()&1);
 APP_Lumos_trigger(0xffffff);assert(off==1&&!(lampState.load()&1));
 APP_Lumos_on(0xffffff);delete pending[1];assert(lampState.load()&1);
 delete pending[2];assert(!(lampState.load()&1));
}
''')

    def test_sampling_flood_leaves_room_for_mode_controls(self):
        self.native({'Arduino.h':r'''
#pragma once
#include <stdint.h>
#include <deque>
#include <mutex>
using QueueHandle_t=struct Queue*;using SemaphoreHandle_t=std::mutex*;
#include <stddef.h>
struct Print{virtual ~Print()=default;virtual size_t write(uint8_t)=0;virtual size_t write(const uint8_t*,size_t)=0;void println(const char*){}template<class...T>void printf(const char*,T...){}};
constexpr unsigned portMAX_DELAY=0xffffffff;
#define pdMS_TO_TICKS(x) (x)
#define pdTRUE 1
#define pdPASS 1
inline auto xSemaphoreCreateMutex(){return new std::mutex;}
inline int xSemaphoreTake(SemaphoreHandle_t m,unsigned){m->lock();return 1;}
inline void xSemaphoreGive(SemaphoreHandle_t m){m->unlock();}
unsigned uxQueueSpacesAvailable(QueueHandle_t);
int xQueueSend(QueueHandle_t,const void*,unsigned);
'''},r'''
#include "Service/EventBus.h"
#include "Service/DualPrint.h"
#include <cassert>
size_t DualPrint::write(uint8_t){return 1;}size_t DualPrint::write(const uint8_t*,size_t n){return n;}DualPrint DualSerial;
struct Queue{unsigned capacity;std::deque<SystemEvent> events;};
unsigned uxQueueSpacesAvailable(Queue *q){return q->capacity-q->events.size();}
int xQueueSend(Queue *q,const void *p,unsigned){if(!uxQueueSpacesAvailable(q))return 0;q->events.push_back(*static_cast<const SystemEvent*>(p));return 1;}
int main(){
 EventBus::init();Queue app{16};assert(EventBus::subscribe(EVENT_IMU_DATA_UPDATED,&app));assert(EventBus::subscribe(EVENT_APP_SET_MODE,&app));
 for(int i=0;i<10000;++i)EventBus::publish(EVENT_IMU_DATA_UPDATED,1,2);
 assert(app.events.size()==12);assert(EventBus::publish(EVENT_APP_SET_MODE,2));
 assert(app.events.back().id==EVENT_APP_SET_MODE&&app.events.back().param1.i32==2);
}
''',('Service/EventBus.cpp',))

    def test_imu_missing_irq_stall_reset_and_zero_acceleration(self):
        files={
        'HAL_IMU.cpp':(find_template()/'src/HAL/HAL_IMU.cpp').read_text(encoding='utf-8'),
        'Arduino.h':r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
using TaskHandle_t=void*;using QueueHandle_t=void*;using SemaphoreHandle_t=void*;using QueueSetHandle_t=void*;using QueueSetMemberHandle_t=void*;using BaseType_t=int;
constexpr unsigned portMAX_DELAY=0xffffffff;
#define IRAM_ATTR
#define pdTRUE 1
#define pdFALSE 0
#define pdMS_TO_TICKS(x) (x)
#define portYIELD_FROM_ISR()
#define RAD_TO_DEG 57.295779513f
extern uint32_t clockMs;inline uint32_t millis(){return clockMs;}
inline void *xQueueCreate(int,int){return (void*)1;}inline void *xSemaphoreCreateBinary(){return (void*)2;}
inline void *xQueueCreateSet(int){return (void*)3;}inline void xQueueAddToSet(void*,void*){}
inline void xTaskCreatePinnedToCore(void(*)(void*),const char*,int,void*,int,void **handle,int){if(handle)*handle=(void*)4;}
inline void xSemaphoreGiveFromISR(void*,int*){}inline int xSemaphoreTake(void*,unsigned){return 1;}
void *xQueueSelectFromSet(void*,unsigned);inline int xQueueReceive(void*,void*,unsigned){return 0;}
struct Bus{void begin(int,int){}};inline Bus Wire;
''',
        'HAL.h':r'''
#pragma once
#include <Arduino.h>
class AnimStatus;
namespace HAL{void ICM42670P_start(int8_t);void ICM42670P_stop();bool ICM42670P_init();int8_t ICM42670P_getDataMux();bool ICM42670P_ready();bool ICM42670P_idle();uint32_t ICM42670P_samples();uint32_t ICM42670P_sampleAge();uint32_t ICM42670P_recoveries();void enable_AnimStatus();void ws2812_set_overlay(AnimStatus*);}
''',
        'HAL_ICM42670P.h':r'''
#pragma once
#include <Arduino.h>
struct inv_imu_sensor_event_t{};
extern bool emitSample;extern int readCalls,startCalls,startError;
class ICM42670{
public:
 struct IMU_PhysicalData_t{float Ax=0,Ay=0,Az=1,Gx=0,Gy=0,Gz=0;};
 inline static IMU_PhysicalData_t *physical=nullptr;
 ICM42670(Bus&,int,int){}int begin(){return 0;}int enterSleepMode(){return 0;}
 int enableDataFromFifoInterrupt(int,void(*)()){++startCalls;return startError;}
 int startWakeOnMotion(int){return 0;}
 int getDataFromFifo(void(*cb)(inv_imu_sensor_event_t*)){++readCalls;if(emitSample){inv_imu_sensor_event_t e;cb(&e);}return 0;}
 void convertToPhysical(inv_imu_sensor_event_t*,IMU_PhysicalData_t &data){data=*physical;}
 bool isAccelDataValid(inv_imu_sensor_event_t*){return true;}bool isGyroDataValid(inv_imu_sensor_event_t*){return true;}
};
''',
        'Config.h':'#pragma once\n#define PIN_IMU_SDA 1\n#define PIN_IMU_SCL 2\n#define PIN_IMU_INT 3\n',
        'Service/AP.h':'#pragma once\n',
        'Service/EventBus.h':r'''
#pragma once
#include <Arduino.h>
enum EventID{EVENT_IMU_SET_MUX,EVENT_IMU_RESET_MUX,EVENT_IMU_SET_WOM,EVENT_IMU_STATUS_CHANGED,EVENT_IMU_DATA_UPDATED};
union Param{int i32;float f32;};struct SystemEvent{EventID id;Param param1,param2;};
struct EventBus{template<class...T>static bool publish(T...){return true;}static void subscribe(EventID,void*){}};
struct Log{template<class T>void println(T){}void print(const char*){}template<class...T>void printf(const char*,T...){}};inline Log DualSerial;
''',
        'Model/gesture_buffer.h':'#pragma once\nextern int resets;inline uint32_t resetGestureBuffer(){return ++resets;}inline void addSample(float,float,bool){}\n',
        'HAL/WS2812_Animation/AnimStatus.hpp':'#pragma once\nstruct AnimStatus{AnimStatus(const volatile int8_t&){}};\n',
        }
        self.native(files,r'''
#include "HAL_IMU.cpp"
#include <cassert>
#include <cmath>
uint32_t clockMs=0;bool emitSample=true;int readCalls=0,startCalls=0,startError=0,resets=0,selects=0;
struct Done{};
void *xQueueSelectFromSet(void*,unsigned wait){assert(wait==50);if(selects++)throw Done{};return nullptr;}
namespace HAL{void ws2812_set_overlay(AnimStatus *s){delete s;}}
void cycle(){selects=0;try{icm42670p_task(nullptr);}catch(Done&){} }
int main(){
 ICM42670::IMU_PhysicalData_t data;ICM42670::physical=&data;
 assert(HAL::ICM42670P_init());HAL::ICM42670P_start(2);assert(startCalls==1);
 clockMs=100;inv_imu_sensor_event_t e;event_cb(&e);assert(HAL::ICM42670P_ready());
 int before=resets;HAL::ICM42670P_start(2);assert(resets==before+1&&startCalls==1);
 data.Az=0;event_cb(&e);assert(HAL::ICM42670P_samples()==1&&std::isfinite(q0));
 data.Az=1;clockMs=200;cycle();assert(readCalls==1&&HAL::ICM42670P_samples()==2);
 emitSample=false;clockMs=1500;cycle();assert(startCalls==2&&HAL::ICM42670P_recoveries()==1);
 emitSample=true;clockMs=1600;cycle();assert(HAL::ICM42670P_ready());
 HAL::ICM42670P_stop();assert(!HAL::ICM42670P_ready()&&HAL::ICM42670P_getDataMux()==-1);
 startError=-1;HAL::ICM42670P_start(2);assert(HAL::ICM42670P_getDataMux()==-1);
 startError=0;HAL::ICM42670P_start(2);clockMs=1700;cycle();assert(HAL::ICM42670P_ready());
}
''')

    def test_renderer_stop_owns_cleanup_while_producers_are_active(self):
        self.native({'HAL_WS2812.cpp':(find_template()/'src/HAL/HAL_WS2812.cpp').read_text(encoding='utf-8'),
        'HAL.h':'#pragma once\nclass AnimationBase;namespace HAL{void ws2812_init();void ws2812_stop();void ws2812_set_background(AnimationBase*);void ws2812_start_fx(AnimationBase*);void ws2812_set_overlay(AnimationBase*);}\n',
        'Config.h':'#pragma once\n#define WS2812_LED_COUNT 39\n#define PIN_WS2812 38\n#define PIN_WS2812_EN 39\n',
        'Arduino.h':r'''
#pragma once
#include <stdint.h>
#include <thread>
using QueueHandle_t=struct Queue*;using SemaphoreHandle_t=struct Semaphore*;using TaskHandle_t=std::thread*;using TickType_t=uint32_t;
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
#define INPUT 0
#define OUTPUT 1
inline void pinMode(int,int){}inline void digitalWrite(int,int){}inline uint32_t millis(){return 0;}
QueueHandle_t xQueueCreate(int,int);int xQueueReceive(QueueHandle_t,void*,unsigned);int xQueueSend(QueueHandle_t,const void*,unsigned);
SemaphoreHandle_t xSemaphoreCreateMutex();SemaphoreHandle_t xSemaphoreCreateBinary();int xSemaphoreTake(SemaphoreHandle_t,unsigned);void xSemaphoreGive(SemaphoreHandle_t);
void xTaskCreatePinnedToCore(void(*)(void*),const char*,int,void*,int,TaskHandle_t*,int);void vTaskDelete(void*);
inline uint32_t xTaskGetTickCount(){return 0;}inline void vTaskDelayUntil(uint32_t*,unsigned){std::this_thread::sleep_for(std::chrono::milliseconds(1));}
''',
        'freertos/FreeRTOS.h':'#pragma once\n#include <Arduino.h>\n',
        'freertos/task.h':'#pragma once\n#include <Arduino.h>\n',
        'freertos/queue.h':'#pragma once\n#include <Arduino.h>\n',
        'FastLED.h':FASTLED+r'''
constexpr int WS2812=0,GRB=0;
struct Driver{template<int,int,int>void addLeds(CRGB*,int){}void setBrightness(int){}void clear(bool=false){}void show(){}};inline Driver FastLED;
inline void fill_solid(CRGB *p,int n,CRGB c){while(n--)*p++=c;}
'''},r'''
#include "HAL_WS2812.cpp"
#include <cassert>
#include <deque>
#include <mutex>
#include <condition_variable>
struct Queue{unsigned capacity;std::mutex lock;std::deque<layer_cmd_t> items;};
Queue *xQueueCreate(int capacity,int){auto *q=new Queue;q->capacity=capacity;return q;}
int xQueueReceive(Queue *q,void *out,unsigned){std::lock_guard<std::mutex> l(q->lock);if(q->items.empty())return 0;*static_cast<layer_cmd_t*>(out)=q->items.front();q->items.pop_front();return 1;}
int xQueueSend(Queue *q,const void *item,unsigned){std::lock_guard<std::mutex> l(q->lock);if(q->items.size()>=q->capacity)return 0;q->items.push_back(*static_cast<const layer_cmd_t*>(item));return 1;}
struct Semaphore{std::mutex lock;std::condition_variable cv;int available=0;};
Semaphore *xSemaphoreCreateMutex(){auto *s=new Semaphore;s->available=1;return s;}
Semaphore *xSemaphoreCreateBinary(){return new Semaphore;}
int xSemaphoreTake(Semaphore *s,unsigned ms){std::unique_lock<std::mutex> l(s->lock);if(!s->cv.wait_for(l,std::chrono::milliseconds(ms),[&]{return s->available>0;}))return 0;--s->available;return 1;}
void xSemaphoreGive(Semaphore *s){{std::lock_guard<std::mutex> l(s->lock);++s->available;}s->cv.notify_one();}
struct Exit{};void vTaskDelete(void*){throw Exit{};}
void xTaskCreatePinnedToCore(void(*f)(void*),const char*,int,void *arg,int,TaskHandle_t *out,int){*out=new std::thread([=]{try{f(arg);}catch(Exit&){};});}
std::atomic<int> made{0},destroyed{0};
struct Animation:AnimationBase{Animation(){++made;}~Animation(){++destroyed;}void update(CRGB*,uint16_t)override{}};
int main(){
 HAL::ws2812_init();auto *renderer=ws2812_task_handle;
 std::thread producer([]{for(int i=0;i<10000;++i)HAL::ws2812_start_fx(new Animation);});
 std::this_thread::sleep_for(std::chrono::milliseconds(2));HAL::ws2812_stop();producer.join();renderer->join();delete renderer;
 assert(made==destroyed);assert(!ws2812_task_handle&&layer_queue->items.empty());
 delete layer_queue;delete layerMutex;delete stopped;
}
''')

    def test_firmware_upload_commits_only_after_complete_request(self):
        self.native({'OTA.cpp':(find_template()/'src/Service/OTA.cpp').read_text(encoding='utf-8'),
        'Arduino.h':r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string>
#include <algorithm>
#include <cctype>
using String=std::string;using std::min;
struct IPAddress{};extern uint32_t clockMs;inline uint32_t millis(){return clockMs;}
struct Device{int restarts=0;void restart(){++restarts;}};inline Device ESP;
struct Exit{};inline void vTaskDelay(int){throw Exit{};}
inline void xTaskCreatePinnedToCore(void(*)(void*),const char*,int,void*,int,void*,int){}
''',
        'OTA.h':'#pragma once\n#include <Arduino.h>\nvoid OTA_begin();bool OTA_active();\n',
        'Config.h':'#pragma once\n#define AP_SSID "Wand"\n#define AP_PASS "12345678"\n',
        'HAL/HAL.h':'#pragma once\nnamespace HAL{template<class T>void ws2812_set_overlay(T *p){delete p;}}\n',
        'HAL/WS2812_Animation/AnimProgress.hpp':'#pragma once\nstruct AnimProgress{AnimProgress(uint8_t&){}};\n',
        'HAL/WS2812_Animation/AnimBreathe.hpp':'#pragma once\nstruct AnimBreathe{};\n',
        'Service/AP.h':'#pragma once\ninline bool ap_is_running(){return false;}inline bool espnow_is_running(){return false;}inline void ap_toggle(){}inline void espnow_toggle(){}\n',
        'Service/DualPrint.h':'#pragma once\nstruct Log{void println(const char*){}};inline Log DualSerial;\n',
        'WiFi.h':'#pragma once\n#include <Arduino.h>\n#define WIFI_AP 1\nstruct Radio{void mode(int){}void softAP(const char*,const char*){}IPAddress softAPIP(){return {};}};inline Radio WiFi;\n',
        'WebServer.h':r'''
#pragma once
#include <Arduino.h>
#include <functional>
#include <map>
#define HTTP_GET 1
#define HTTP_POST 2
#define UPLOAD_FILE_START 1
#define UPLOAD_FILE_WRITE 2
#define UPLOAD_FILE_END 3
#define UPLOAD_FILE_ABORTED 4
struct HTTPUpload{int status=0;size_t currentSize=0,totalSize=0;uint8_t buf[256]{};};
struct WebServer{
 struct Route{std::function<void()> done,upload;};std::map<std::string,Route> routes;std::map<std::string,String> args;
 int status=0;String body;HTTPUpload packet;
 WebServer(int){}void on(const char *path,int,std::function<void()> done,std::function<void()> upload={}){routes[path]={done,upload};}
 String arg(const char *name){return args[name];}void send(int code,const char*,String value){status=code;body=value;}
 HTTPUpload &upload(){return packet;}size_t clientContentLength(){return 256;}void begin(){}void handleClient(){}
};
''',
        'Update.h':r'''
#pragma once
#include <Arduino.h>
#define U_FLASH 0
#define UPDATE_SIZE_UNKNOWN 0xffffffff
struct Updater{
 bool running=false,writeOK=true,endOK=true;int target=-1,commits=0;
 bool isRunning(){return running;}bool begin(unsigned,int kind){target=kind;running=true;return true;}
 void abort(){running=false;}bool setMD5(const char*){return true;}
 size_t write(uint8_t*,size_t n){return writeOK?n:0;}
 bool end(bool){running=false;if(endOK)++commits;return endOK;}
};inline Updater Update;
'''},r'''
#include "OTA.cpp"
#include <cassert>
uint32_t clockMs=100;
void start(){server.args["mode"]="firmware";server.args["hash"]=std::string(32,'a');server.routes["/ota/start"].done();assert(server.status==200);}
void upload(int status){server.packet.status=status;server.packet.currentSize=100;server.routes["/ota/upload"].upload();}
void finish(){server.routes["/ota/upload"].done();}
int main(){
 OTA_begin();assert(OTA_active());
 server.args["mode"]="filesystem";server.routes["/ota/start"].done();assert(server.status==400&&Update.target==-1);
 server.args["mode"]="firmware";server.args["hash"]="bad";server.routes["/ota/start"].done();assert(server.status==400);
 start();assert(Update.target==U_FLASH);upload(UPLOAD_FILE_START);upload(UPLOAD_FILE_ABORTED);finish();assert(server.status==400&&Update.commits==0);
 start();Update.writeOK=false;upload(UPLOAD_FILE_START);upload(UPLOAD_FILE_WRITE);upload(UPLOAD_FILE_END);finish();assert(server.status==400&&Update.commits==0);Update.writeOK=true;
 start();upload(UPLOAD_FILE_START);upload(UPLOAD_FILE_WRITE);upload(UPLOAD_FILE_END);assert(Update.commits==0);
 upload(UPLOAD_FILE_START);finish();assert(server.status==400&&Update.commits==0);
 start();upload(UPLOAD_FILE_START);upload(UPLOAD_FILE_WRITE);upload(UPLOAD_FILE_END);assert(Update.commits==0);
 finish();assert(server.status==200&&server.body=="OK"&&Update.commits==1);
 clockMs+=2100;try{loopOTA(nullptr);}catch(Exit&){}assert(ESP.restarts==1);
}
''')

    def test_tcp_connections_release_on_async_task_without_leaks(self):
        self.native({'AP.cpp':(find_template()/'src/Service/AP.cpp').read_text(encoding='utf-8'),
        'CommandLine.h':(find_template()/'src/Service/CommandLine.h').read_text(encoding='utf-8'),
        'Arduino.h':r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <mutex>
using TaskHandle_t=void*;using SemaphoreHandle_t=std::recursive_mutex*;
extern void *currentTask;inline auto xTaskGetCurrentTaskHandle(){return currentTask;}
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
constexpr unsigned portMAX_DELAY=0xffffffff;
inline auto xSemaphoreCreateRecursiveMutex(){return new std::recursive_mutex;}
inline int xSemaphoreTakeRecursive(SemaphoreHandle_t m,unsigned){m->lock();return 1;}
inline void xSemaphoreGiveRecursive(SemaphoreHandle_t m){m->unlock();}
''',
        'AP.h':'#pragma once\n#include <Arduino.h>\nvoid ap_toggle();bool ap_is_running();void ap_print(const char*,size_t);\n',
        'HomeAssistant.h':'#pragma once\ninline bool ha_uses_wifi(){return false;}inline void ha_start(){}\n',
        'AP_Web.h':'#pragma once\ninline void ap_web_start(){}inline void ap_web_stop(){}inline void ap_web_print(const char*,size_t){}\n',
        'Config.h':'#pragma once\n#define AP_SSID "Wand"\n#define AP_PASS "12345678"\n',
        'Service/Console.h':'#pragma once\nvoid console_parse(char*);\n',
        'Service/DualPrint.h':'#pragma once\nstruct Log{template<class T>void print(T){}template<class U>void println(U){}template<class...T>void printf(const char*,T...){}};inline Log DualSerial;\n',
        'WiFi.h':'#pragma once\n#include <Arduino.h>\nenum wifi_mode_t{WIFI_MODE_NULL,WIFI_MODE_STA,WIFI_MODE_APSTA};constexpr int WIFI_AP_STA=2,WIFI_STA=1,WIFI_OFF=0;struct Radio{void mode(int){}void softAP(const char*,const char*){}void softAPdisconnect(bool){}int softAPIP(){return 1;}int channel(){return 1;}};inline Radio WiFi;\n',
        'esp_wifi.h':'#pragma once\n#define ESP_OK 0\n',
        'esp_now.h':'#pragma once\nusing esp_now_send_status_t=int;struct esp_now_peer_info_t{uint8_t peer_addr[6];int channel;bool encrypt;};inline int esp_now_init(){return 0;}inline void esp_now_register_send_cb(void(*)(const uint8_t*,int)){}inline void esp_now_add_peer(esp_now_peer_info_t*){}inline void esp_now_deinit(){}inline int esp_now_send(const uint8_t*,const uint8_t*,size_t){return 0;}\n',
        'AsyncTCP.h':r'''
#pragma once
#include <Arduino.h>
#include <set>
#include <cassert>
struct AsyncClient;
inline std::set<AsyncClient*> alive;
struct AsyncClient{
 void(*disconnect)(void*,AsyncClient*)=nullptr;void *arg=nullptr;bool live=true;
 AsyncClient(){alive.insert(this);}~AsyncClient(){assert(alive.erase(this)==1);}
 void onDisconnect(void(*f)(void*,AsyncClient*),void *a=nullptr){disconnect=f;arg=a;}
 void onData(void(*)(void*,AsyncClient*,void*,size_t),void*){}
 bool connected(){return live;}void close(){live=false;if(disconnect)disconnect(arg,this);}
 size_t space(){return 10000;}void add(const char*,size_t){}void send(){}
};
struct AsyncServer{AsyncServer(int address,int){assert(address==WiFi.softAPIP());}void onClient(void(*)(void*,AsyncClient*),void*){}void begin(){}void end(){}};
'''},r'''
#include "AP.cpp"
#include <cassert>
#include <string>
#include <vector>
void *currentTask=(void*)1;std::vector<std::string> commands;
void console_parse(char *s){commands.emplace_back(s);}
int main(){
 start_ap_server();auto *first=new AsyncClient;handleNewClient(nullptr,first);
 const char data[]={'b','a','c'};handleData(nullptr,first,(void*)data,3);handleData(nullptr,first,(void*)"k\n",2);assert(commands.back()=="back");
 currentTask=(void*)2;stop_ap_server();assert(alive.size()==1&&retired[0]==first);
 for(int n=0;n<100;++n){
  currentTask=(void*)1;start_ap_server();auto *a=new AsyncClient;handleNewClient(nullptr,a);assert(alive.size()==1);
  auto *b=new AsyncClient;handleNewClient(nullptr,b);auto *overflow=new AsyncClient;handleNewClient(nullptr,overflow);assert(alive.size()==2);
  ap_print("ok",2);currentTask=(void*)2;stop_ap_server();assert(alive.size()==2);
 }
 currentTask=(void*)1;start_ap_server();auto *last=new AsyncClient;handleNewClient(nullptr,last);assert(alive.size()==1);last->close();assert(alive.empty());
 delete clientsMutex;delete tcpServer;
}
''')

    def test_volume_reentry_starts_from_current_angle(self):
        fastled=FASTLED.replace('static const CRGB White,Black;','static const CRGB White,Black,Blue,Red;')+'\ninline const CRGB CRGB::Blue{0xff},CRGB::Red{0xff0000};\n'
        self.native({'APP_BLE_HID.cpp':(find_template()/'src/APP/APP_BLE_HID.cpp').read_text(encoding='utf-8'),
        'APP_BLE_HID.h':'#pragma once\n',
        'APP_Lumos.h':'#pragma once\n#include <stdint.h>\ninline void APP_Lumos_on(uint32_t){}inline void APP_Lumos_off(){}\n',
        'Service/BLE.h':'#pragma once\n#include <stdint.h>\n#define KEY_UP 0x52\n#define KEY_DOWN 0x51\n#define MEDIA_VOLUME_UP 0xe9\n#define MEDIA_VOLUME_DOWN 0xea\ninline bool ble_keyboard_tap_key(int){return true;}inline bool ble_mouse_move(int,int){return true;}extern int volumeReports;inline bool ble_consumer_send(int){++volumeReports;return true;}\n',
        'Service/DualPrint.h':'#pragma once\nstruct Log{void println(const char*){}};inline Log DualSerial;\n',
        'Service/EventBus.h':'#pragma once\n',
        'FastLED.h':fastled,
        'Arduino.h':'#pragma once\n#include <stdint.h>\nextern uint32_t clockMs;inline uint32_t millis(){return clockMs;}\n#define constrain(x,a,b) ((x)<(a)?(a):(x)>(b)?(b):(x))\n'},r'''
#include "APP_BLE_HID.cpp"
#include <cassert>
#include <cmath>
uint32_t clockMs=0;int volumeReports=0;
int main(){
 APP_ble_volume_mode_enter();APP_ble_volume_knob(0);clockMs=200;APP_ble_volume_knob(10);assert(volumeReports>0);
 APP_ble_volume_mode_exit();clockMs=1200;APP_ble_volume_mode_enter();volumeReports=0;APP_ble_volume_knob(20);assert(volumeReports==0);
 APP_ble_volume_knob(NAN);clockMs=1400;APP_ble_volume_knob(20);assert(volumeReports==0);
 clockMs=1600;APP_ble_volume_knob(25);clockMs=1800;APP_ble_volume_knob(30);assert(volumeReports>0);
}
''')

    def test_imu_fifo_batch_read_has_one_balanced_transaction(self):
        self.native({},r"""
#include "HAL/ImuI2c.h"
#include <cassert>
#include <cstring>
struct Wire {
 int starts=0,reads=0;bool locked=false,repeated=false,shortRead=false,writeFail=false;
 void beginTransmission(uint8_t){assert(!locked);locked=true;++starts;}
 size_t write(uint8_t reg){assert(locked&&reg==0x29);return writeFail?0:1;}
 int endTransmission(bool stop){assert(locked);if(stop)locked=false;else repeated=true;return 0;}
 size_t requestFrom(uint8_t,size_t n,bool stop){assert(locked&&repeated&&stop);locked=repeated=false;++reads;return shortRead?n-1:n;}
 size_t readBytes(char *out,size_t n){assert(!locked);memset(out,0x55,n);return n;}
};
int main(){
 Wire wire;uint8_t out[2560]{};
 assert(readImuRegister(wire,0x68,0x29,out,sizeof(out))==0);
 assert(wire.starts==1&&wire.reads==1&&out[2559]==0x55&&!wire.locked);
 wire.shortRead=true;assert(readImuRegister(wire,0x68,0x29,out,48)==-1&&!wire.locked);
 wire.shortRead=false;assert(readImuRegister(wire,0x68,0x29,out,48)==0);
 wire.writeFail=true;assert(readImuRegister(wire,0x68,0x29,out,48)==-1&&!wire.locked);
}
""")
