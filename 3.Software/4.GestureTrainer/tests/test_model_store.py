"""Actual device bank/validation code, simulated NOR flash and operator validation.
SHA-256 uses host OpenSSL; these checks never open hardware or application OTA.
"""
import hashlib,os,shutil,struct,subprocess,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import find_template
from model_package import encode,decode
from workflow_core import PROFILE,GESTURES,profile_key
from test_runtime_bindings import PREFERENCES

ARDUINO=r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <algorithm>
using std::min;using SemaphoreHandle_t=void*;using portMUX_TYPE=int;
#define portMUX_INITIALIZER_UNLOCKED 0
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
#define pdMS_TO_TICKS(x) (x)
#define pdPASS 1
#define pdTRUE 1
inline void*vTaskDelete(void*){return nullptr;}inline void vTaskDelay(int){}
inline int xTaskCreatePinnedToCore(void(*fn)(void*),const char*,int,void*p,int,void**,int){fn(p);return 1;}
inline void*xSemaphoreCreateMutex(){return reinterpret_cast<void*>(1);}inline int xSemaphoreTake(void*,int){return 1;}inline void xSemaphoreGive(void*){}
inline uint32_t esp_random(){return 123;}
inline size_t test_strlcpy(char*d,const char*s,size_t n){auto size=strlen(s);if(n){memcpy(d,s,std::min(n-1,size));d[std::min(n-1,size)]=0;}return size;}
#define strlcpy test_strlcpy
struct String{std::string data;String(const char*p):data(p){}String&operator+=(char c){data+=c;return *this;}String&operator+=(const char*p){data+=p;return *this;}const char*c_str()const{return data.c_str();}size_t length()const{return data.size();}};
struct ESPType{void restart(){}};inline ESPType ESP;
'''
PARTITION=r'''
#pragma once
#include <stdint.h>
#include <cstring>
#include <fstream>
#include <vector>
#include <cassert>
#define ESP_OK 0
#define ESP_PARTITION_TYPE_DATA 1
#define ESP_PARTITION_SUBTYPE_DATA_SPIFFS 0x82
struct esp_partition_t{size_t size=0x20000;};inline esp_partition_t part;
inline std::vector<uint8_t>flash(0x20000,255);inline long writeBudget=-1;
inline const esp_partition_t*esp_partition_find_first(int,int,const char*){return &part;}
inline int esp_partition_read(const esp_partition_t*,size_t off,void*out,size_t n){if(off+n>flash.size())return -1;memcpy(out,flash.data()+off,n);return 0;}
inline int esp_partition_erase_range(const esp_partition_t*,size_t off,size_t n){if(off%4096||n%4096||off+n>flash.size())return -1;memset(flash.data()+off,255,n);return 0;}
inline int esp_partition_write(const esp_partition_t*,size_t off,const void*in,size_t n){if(off+n>flash.size())return -1;size_t take=writeBudget<0?n:std::min(n,static_cast<size_t>(writeBudget));auto p=static_cast<const uint8_t*>(in);for(size_t i=0;i<take;++i){if((flash[off+i]&p[i])!=p[i])return -1;flash[off+i]=p[i];}if(writeBudget>=0)writeBudget-=take;return take==n?0:-1;}
'''
SHA=r'''
#pragma once
#include <openssl/sha.h>
using mbedtls_sha256_context=SHA256_CTX;
inline void mbedtls_sha256_init(SHA256_CTX*){}inline void mbedtls_sha256_free(SHA256_CTX*){}
inline int mbedtls_sha256_starts_ret(SHA256_CTX*c,int){return SHA256_Init(c)?0:-1;}
inline int mbedtls_sha256_update_ret(SHA256_CTX*c,const unsigned char*p,size_t n){return SHA256_Update(c,p,n)?0:-1;}
inline int mbedtls_sha256_finish_ret(SHA256_CTX*c,unsigned char*out){return SHA256_Final(out,c)?0:-1;}
inline int mbedtls_sha256_ret(const unsigned char*p,size_t n,unsigned char*out,int){return SHA256(p,n,out)?0:-1;}
'''
HARNESS=r'''
#include "Model/ModelStore.h"
#include "Service/GestureBindings.h"
#include <esp_partition.h>
#include <fstream>
#include <iterator>
#include <cassert>
#include <string>
bool inference_validate_model(const uint8_t*p,size_t n,int,char*e,size_t cap){if(n>=9&&p[8]!=255)return true;strlcpy(e,"unsupported operator",cap);return false;}
std::vector<uint8_t> read(const char*file){std::ifstream f(file,std::ios::binary);return std::vector<uint8_t>(std::istreambuf_iterator<char>(f),{});}
void upload(const std::vector<uint8_t>&p,bool expected){uint32_t ticket;void*owner=reinterpret_cast<void*>(1);assert(ModelStore::reserve(owner,ticket));auto *data=ModelStore::allocate(p.size());memcpy(data,p.data(),p.size());assert(ModelStore::submit(owner,data,p.size()));assert((std::string(ModelStore::status().phase)=="complete")==expected);}
int main(int argc,char**argv){
 auto a=read(argv[1]),b=read(argv[2]),c=read(argv[3]),bad=read(argv[4]);
 ModelStore::init();assert(!ModelStore::from_storage());assert(gesture_model_count()==8);
 upload(a,true);ModelStore::init();gesture_bindings_init();assert(ModelStore::from_storage());assert(gesture_model_count()==8);
 int8_t actions[16],effects[16];for(int i=0;i<16;++i){actions[i]=-1;effects[i]=-1;}actions[0]=5;effects[0]=21;
 assert(gesture_bindings_save(actions,effects,1)==BindingSave::Saved);
 auto baseline=flash;
 for(long budget:{0L,1024L,long(b.size()),long(b.size()+52),long(b.size()+62)}){
  flash=baseline;writeBudget=budget;upload(b,false);writeBudget=-1;ModelStore::init();assert(gesture_model_count()==8);assert(!strcmp(gesture_model_key(),"letters-v1"));
 }
 auto before=flash;upload(bad,false);assert(flash==before);ModelStore::init();
 uint32_t ticket;assert(ModelStore::reserve(reinterpret_cast<void*>(1),ticket));assert(!ModelStore::reserve(reinterpret_cast<void*>(2),ticket));ModelStore::cancel(reinterpret_cast<void*>(2));assert(ModelStore::busy());ModelStore::cancel(reinterpret_cast<void*>(1));assert(!ModelStore::busy());
 upload(b,true);ModelStore::init();gesture_bindings_init();assert(gesture_model_count()==9);assert(!strcmp(gesture_model_key(),argv[5]));
 auto state=gesture_bindings_state();assert(state.actions[0]==5&&state.effects[0]==21&&state.actions[8]==-1&&state.effects[8]==-1);
 state.actions[8]=1;state.effects[8]=25;assert(gesture_bindings_save(state.actions,state.effects,state.revision)==BindingSave::Saved);
 upload(c,true);ModelStore::init();gesture_bindings_init();state=gesture_bindings_state();assert(gesture_model_count()==9&&state.actions[8]==1&&state.effects[8]==25);
 // Corrupt the latest bank payload. Boot must recover the previous valid bank.
 flash[64+1088+10]^=128;ModelStore::init();assert(gesture_model_count()==9);gesture_bindings_init();assert(gesture_bindings_state().effects[8]==25);
 char error[180];auto invalid=b;invalid[0]^=1;assert(!ModelStore::validate(invalid.data(),invalid.size(),error,sizeof(error)));
}
'''

@unittest.skipUnless(os.name!='nt' and shutil.which('g++') and Path('/usr/include/openssl/sha.h').exists(),'Host flash test uses Linux/OpenSSL')
class ModelStoreTests(unittest.TestCase):
    def test_actual_banks_interruption_validation_and_mapping_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Model').mkdir();(root/'Service').mkdir();(root/'mbedtls').mkdir()
            firmware=find_template()
            for name in ['ModelStore.h','ModelStore.cpp','ModelCatalog.h']:shutil.copy2(firmware/'src/Model'/name,root/'Model'/name)
            shutil.copy2(firmware/'src/Service/GestureBindings.h',root/'Service/GestureBindings.h');shutil.copy2(firmware/'src/Service/GestureEffects.h',root/'Service/GestureEffects.h')
            (root/'Model/GestureProfile.h').write_text('#pragma once\n#include <stdint.h>\nconstexpr int kGestureCount=8;constexpr const char*kGestureProfileKey="letters-v1";constexpr const char*kGestureLabels[]={"a","e","n","o","p","s","8","∞"};constexpr int8_t kDefaultGestureActions[8]={-1,-1,-1,-1,-1,-1,-1,-1};\n')
            (root/'Model/BuiltinModelProfile.h').write_text('#pragma once\nconstexpr float kBuiltinMean[2]={0,0},kBuiltinStd[2]={1,1};\n')
            (root/'Model/gesture_model.h').write_text('#pragma once\nalignas(16) const unsigned char gesture_model_tflite[]={0,0,0,0,84,70,76,51,0};constexpr unsigned int gesture_model_tflite_len=9;\n')
            (root/'Arduino.h').write_text(ARDUINO);(root/'esp_partition.h').write_text(PARTITION);(root/'mbedtls/sha256.h').write_text(SHA);(root/'Preferences.h').write_text(PREFERENCES)
            (root/'esp_heap_caps.h').write_text('#pragma once\n#include <cstdlib>\n#define MALLOC_CAP_SPIRAM 1\n#define MALLOC_CAP_8BIT 2\ninline void*heap_caps_aligned_alloc(size_t a,size_t n,int){void*p=nullptr;return posix_memalign(&p,a,n)?nullptr:p;}\n')
            paths=[]
            for names,tag in [(GESTURES,1),(GESTURES+['z'],2),(GESTURES+['z'],3),(GESTURES+['z'],255)]:
                blob=b'\x1c\0\0\0TFL3'+bytes([tag])*200
                report=dict(profile=PROFILE,gestures=names,counts=[120]*len(names),mean=[0,0],std=[1,1],model_sha256=hashlib.sha256(blob).hexdigest())
                path=root/f'package{tag}';path.write_bytes(encode(blob,report));paths.append(path)
            (root/'test.cpp').write_text(HARNESS)
            exe=root/'banks';compiled=subprocess.run(['g++','-std=c++17','-Wno-deprecated-declarations','-I'+str(root),'-I'+str(firmware/'src'),str(root/'Model/ModelStore.cpp'),str(firmware/'src/Service/GestureBindings.cpp'),str(root/'test.cpp'),'-lcrypto','-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            storage=root/'nvs';storage.mkdir();result=subprocess.run([str(exe),*map(str,paths),profile_key(GESTURES+['z'])],env=dict(os.environ,WAND_TEST_NVS=str(storage)),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
