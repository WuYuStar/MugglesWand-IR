"""Run the actual firmware mapping service on the host with disk-backed NVS.
Hardware flash/Preferences and FreeRTOS primitives are substituted, not the
parsing, validation, profile separation, commit or inference lookup logic.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import find_template
FIRMWARE=find_template()

ARDUINO=r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <mutex>
using SemaphoreHandle_t=std::mutex*;
using portMUX_TYPE=std::mutex;
#define portMUX_INITIALIZER_UNLOCKED {}
#define pdTRUE 1
#define pdMS_TO_TICKS(x) (x)
inline auto xSemaphoreCreateMutex(){return new std::mutex;}
inline int xSemaphoreTake(std::mutex*m,int){m->lock();return 1;}
inline void xSemaphoreGive(std::mutex*m){m->unlock();}
#define portENTER_CRITICAL(m) (m)->lock()
#define portEXIT_CRITICAL(m) (m)->unlock()
'''
PREFERENCES=r'''
#pragma once
#include <fstream>
#include <filesystem>
#include <cstdlib>
#include <string>
inline bool failWrite=false;
class Preferences {
 std::string ns;
 std::string path(const char*k){return std::string(std::getenv("WAND_TEST_NVS"))+"/"+ns+"/"+k;}
 public:
 bool begin(const char*name,bool readonly){ns=name;auto dir=std::string(std::getenv("WAND_TEST_NVS"))+"/"+ns;if(readonly)return std::filesystem::exists(dir);std::filesystem::create_directories(dir);return true;}
 size_t getBytesLength(const char*k){auto p=path(k);return std::filesystem::exists(p)?std::filesystem::file_size(p):0;}
 size_t getBytes(const char*k,void*b,size_t n){std::ifstream f(path(k),std::ios::binary);f.read((char*)b,n);return f.gcount();}
 size_t putBytes(const char*k,const void*b,size_t n){if(failWrite)return 0;std::ofstream f(path(k),std::ios::binary);f.write((const char*)b,n);return f?n:0;}
};
'''
CATALOG=r"""
#include "Model/GestureProfile.h"
inline int gesture_model_count(){return kGestureCount;}
inline const char*gesture_model_key(){return kGestureProfileKey;}
inline int gesture_model_default_action(int i){return kDefaultGestureActions[i];}
inline int gesture_model_fallback_count(){return kGestureFallbackCount;}
inline const char*gesture_model_fallback_key(int i){return kGestureFallbacks[i].key;}
inline int gesture_model_fallback_size(int i){return kGestureFallbacks[i].count;}
"""

HARNESS=r'''
#include "Service/GestureBindings.h"
#include "Model/GestureProfile.h"
#include <Preferences.h>
#include <cassert>
#include <string>
#include <cstring>
int main(int argc,char**argv){
 gesture_bindings_init();auto s=gesture_bindings_state();assert(s.writable);
 std::string mode=argv[1];
 if(mode=="defaults"){for(int i=0;i<8;++i)assert(s.actions[i]==kDefaultGestureActions[i]);}
 if(mode=="save"){
  int8_t v[8]={-1,7,0,1,4,6,5,4};uint32_t r=s.revision;
  assert(gesture_bindings_save(v,r)==BindingSave::Saved);
  assert(gesture_binding_action(6)==5&&gesture_binding_action(7)==4);
  assert(gesture_binding_action(-1)==-1&&gesture_binding_action(8)==-1);
  assert(gesture_bindings_save(v,r)==BindingSave::Conflict);
  auto current=gesture_bindings_state();assert(current.revision==r+1);
  assert(gesture_bindings_save(v,current.revision)==BindingSave::Saved);
  assert(gesture_bindings_state().revision==current.revision);
  v[0]=12;assert(gesture_bindings_save(v,current.revision)==BindingSave::Invalid);
  assert(gesture_binding_action(0)==-1);
 }
 if(mode=="ha-save"){
  int8_t a[8],fx[8];uint32_t revision;
  assert(gesture_bindings_parse("web:bindings 1 8,9,10,11,-1,-1,5,4 5,-1,-1,-1,-1,-1,-1,-1",a,fx,revision));
  assert(gesture_bindings_save(a,fx,revision)==BindingSave::Saved);
 }
 if(mode=="retired"){assert(s.actions[4]==-1&&s.actions[5]==-1&&s.actions[6]==5&&s.effects[4]==21);}
 if(mode=="ha-load"){assert(s.actions[0]==8&&s.actions[3]==11&&s.effects[0]==5);}
 if(mode=="load"){assert(s.actions[6]==5&&s.actions[7]==4&&s.actions[0]==-1);}
 if(mode=="fail"){
  failWrite=true;int8_t v[8]={7,7,7,7,7,7,7,7};
  assert(gesture_bindings_save(v,s.revision)==BindingSave::StorageError);
  assert(memcmp(s.actions,gesture_bindings_state().actions,8)==0);
  assert(s.revision==gesture_bindings_state().revision);
 }
 if(mode=="parse"){
  int8_t a[8];uint32_t r;
  assert(gesture_bindings_parse("web:bindings 4294967295 -1,0,1,6,7,4,5,7",a,r)&&r==UINT32_MAX&&a[0]==-1);
  for(const char*c:{"web:bindings 1 2,3,0,0,0,0,0,0","web:bindings 4294967296 0,0,0,0,0,0,0,0","web:bindings -1 0,0,0,0,0,0,0,0","web:bindings 1 0,0,0,0,0,0,0,12","web:bindings 1 0,0,0,0,0,0,0,-2","web:bindings 1 0,0,0,0,0,0,0","web:bindings 1 0,0,0,0,0,0,0,0,1","web:bindings 1 0,0,0,0,0,0,0,00","web:bindings 1 0,0,0,0,0,0,0,0 junk","web:bindings 1 ","web:bindings"})
   assert(!gesture_bindings_parse(c,a,r));
 }
}
'''

@unittest.skipUnless(shutil.which('g++'),'Native firmware test requires g++')
class RuntimeBindingsTests(unittest.TestCase):
    def test_persistence_profile_isolation_failed_commit_and_strict_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Model').mkdir()
            (root/'Arduino.h').write_text(ARDUINO)
            (root/'Preferences.h').write_text(PREFERENCES)
            (root/'catalog.h').write_text(CATALOG)
            (root/'harness.cpp').write_text(HARNESS)
            executables=[]
            for key,defaults in [('legacy-v1',list(range(8))),('letters-v1',[-1]*8)]:
                (root/'Model/GestureProfile.h').write_text('#pragma once\n#include <stdint.h>\nconstexpr int kGestureCount=8;\nstruct GestureProfileFallback {const char*key;int count;};\nconstexpr int kGestureFallbackCount=0;\nconstexpr GestureProfileFallback kGestureFallbacks[]={{"",0}};\nconstexpr const char*kGestureProfileKey="'+key+'";\nconstexpr int8_t kDefaultGestureActions[8]={'+','.join(map(str,defaults))+'};\n')
                exe=root/(key+('.exe' if os.name=='nt' else ''))
                compiled=subprocess.run(['g++','-std=c++17','-pthread','-include',str(root/'catalog.h'),'-I'+str(root),'-I'+str(FIRMWARE/'src'),str(FIRMWARE/'src/Service/GestureBindings.cpp'),str(root/'harness.cpp'),'-o',str(exe)],capture_output=True,text=True)
                self.assertEqual(compiled.returncode,0,compiled.stderr)
                executables.append(exe)
            storage=root/'nvs';storage.mkdir()
            env=dict(os.environ,WAND_TEST_NVS=str(storage))
            for index,mode in [(0,'defaults'),(0,'parse'),(0,'save'),(0,'load'),(1,'defaults'),(1,'save'),(1,'load'),(1,'fail'),(1,'load'),(0,'load')]:
                with self.subTest(profile=index,mode=mode):subprocess.run([str(executables[index]),mode],check=True,env=env,capture_output=True)
            old = storage/'wand-map2'/'letters-v1'
            old.parent.mkdir()
            old.write_bytes(bytes([0x4d,0x57,2,8,255,7,0,1,2,3,5,4]+[255]*4+[21]+[255]*3))
            (storage/'wand-map3'/'letters-v1').unlink()
            subprocess.run([str(executables[1]),'retired'],check=True,env=env,capture_output=True)
            snapshot = old.read_bytes()
            subprocess.run([str(executables[1]),'ha-save'],check=True,env=env,capture_output=True)
            subprocess.run([str(executables[1]),'ha-load'],check=True,env=env,capture_output=True)
            self.assertEqual(old.read_bytes(),snapshot)
            old.unlink()
            # A malformed NVS blob must fall back to that profile's defaults.
            for malformed in [b'\x07'*7,b'\x08'*8]:
                (storage/'wand-map3'/'letters-v1').write_bytes(malformed)
                subprocess.run([str(executables[1]),'defaults'],check=True,env=env,capture_output=True)

    def test_sixteen_gestures_migrate_prefix_and_roundtrip_long_mapping(self):
        from workflow_core import GESTURES,PROFILE,prepare_runtime_firmware
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'src/Model').mkdir(parents=True)
            prepare_runtime_firmware(root,FIRMWARE,dict(profile=PROFILE,gestures=GESTURES+['z','u','v','w','c','d','l','m'],counts=[120]*16,mean=[0,0],std=[1,1]))
            (root/'Arduino.h').write_text(ARDUINO);(root/'Preferences.h').write_text(PREFERENCES)
            (root/'catalog.h').write_text(CATALOG)
            (root/'harness.cpp').write_text(r'''
#include "Service/GestureBindings.h"
#include <cassert>
#include <cstdlib>
int main(int argc,char**argv){
 gesture_bindings_init();auto s=gesture_bindings_state();assert(kGestureCount==16);
 if(argc==1){for(int i=0;i<8;i++)assert(s.actions[i]==5);for(int i=8;i<16;i++)assert(s.actions[i]==-1);
 if(std::getenv("WAND_EXPECT_FX_PREFIX")){for(int i=0;i<8;++i)assert(s.effects[i]==21);for(int i=8;i<16;++i)assert(s.effects[i]==-1);}
 int8_t a[16];uint32_t revision;
 assert(gesture_bindings_parse("web:bindings 1 -1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,7",a,revision));
 int8_t fx[16];for(int i=0;i<16;++i)fx[i]=25;
 assert(gesture_bindings_save(a,fx,revision)==BindingSave::Saved);assert(gesture_binding_action(15)==7);
 }else{assert(s.actions[0]==-1&&s.actions[15]==7&&s.effects[0]==25&&s.effects[15]==25);}
 assert(gesture_binding_action(16)==-1);
}
''')
            exe=root/('mapping16.exe' if os.name=='nt' else 'mapping16')
            build=subprocess.run(['g++','-std=c++17','-pthread','-include',str(root/'catalog.h'),'-I'+str(root),'-I'+str(root/'src'),'-I'+str(FIRMWARE/'src'),str(FIRMWARE/'src/Service/GestureBindings.cpp'),str(root/'harness.cpp'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr)
            storage=root/'nvs';storage.mkdir();(storage/'wand-map').mkdir();(storage/'wand-map'/'letters-v1').write_bytes(bytes([5]*8));env=dict(os.environ,WAND_TEST_NVS=str(storage))
            subprocess.run([str(exe)],env=env,check=True,capture_output=True)
            subprocess.run([str(exe),'reload'],env=env,check=True,capture_output=True)
            for path in (storage/'wand-map3').glob('*'):path.unlink()
            (storage/'wand-map2').mkdir()
            (storage/'wand-map2/letters-v1').write_bytes(bytes([0x4d,0x57,2,8]+[5]*8+[21]*8))
            subprocess.run([str(exe)],env=dict(env,WAND_EXPECT_FX_PREFIX='1'),check=True,capture_output=True)

    def test_atomic_effects_legacy_upgrade_reboot_and_failed_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'Model').mkdir()
            (root/'Arduino.h').write_text(ARDUINO);(root/'Preferences.h').write_text(PREFERENCES)
            (root/'catalog.h').write_text(CATALOG)
            (root/'Model/GestureProfile.h').write_text('#pragma once\n#include <stdint.h>\nconstexpr int kGestureCount=8;\nconstexpr const char*kGestureProfileKey="letters-v1";\nconstexpr int8_t kDefaultGestureActions[8]={-1,-1,-1,-1,-1,-1,-1,-1};\nstruct GestureProfileFallback{const char*key;int count;};\nconstexpr int kGestureFallbackCount=0;constexpr GestureProfileFallback kGestureFallbacks[]={{"",0}};\n')
            (root/'harness.cpp').write_text(r'''
#include "Service/GestureBindings.h"
#include <Preferences.h>
#include <cassert>
#include <cstring>
#include <string>
int main(int argc,char**argv){
 gesture_bindings_init();auto s=gesture_bindings_state();std::string mode=argv[1];
 if(mode=="migrate"){for(int i=0;i<8;++i){assert(s.actions[i]==5);assert(s.effects[i]==-1);}}
 if(mode=="save"){
  int8_t a[8],fx[8];uint32_t revision;
  assert(gesture_bindings_parse("web:bindings 1 -1,0,1,6,7,4,5,6 5,18,25,0,-1,12,21,8",a,fx,revision));
  assert(gesture_bindings_save(a,fx,revision)==BindingSave::Saved);
  s=gesture_bindings_state();assert(s.actions[0]==-1&&s.effects[0]==5&&s.effects[2]==25);
  assert(gesture_bindings_save(a,fx,revision)==BindingSave::Conflict);
  assert(gesture_bindings_save(a,fx,s.revision)==BindingSave::Saved);assert(gesture_bindings_state().revision==s.revision);
  for(const char*bad:{"web:bindings 2 -1,0,1,6,7,4,5,6 26,18,25,0,-1,12,21,8","web:bindings 2 -1,0,1,6,7,4,5,6 -2,18,25,0,-1,12,21,8","web:bindings 2 -1,0,1,6,7,4,5,6 5,18,25,0,-1,12,21","web:bindings 2 -1,0,1,6,7,4,5,6 05,18,25,0,-1,12,21,8","web:bindings 2 -1,0,1,6,7,4,5,6 5,18,25,0,-1,12,21,8 junk"})assert(!gesture_bindings_parse(bad,a,fx,revision));
 }
 if(mode=="load"||mode=="fail"||mode=="legacy"){
  assert(s.actions[0]==-1&&s.effects[0]==5&&s.effects[2]==25);
  if(mode=="fail"){failWrite=true;auto a=s.actions;auto fx=s.effects;fx[0]=6;assert(gesture_bindings_save(a,fx,s.revision)==BindingSave::StorageError);auto after=gesture_bindings_state();assert(after.effects[0]==5&&after.actions[0]==-1&&after.revision==s.revision);}
  if(mode=="legacy"){int8_t a[8],fx[8];uint32_t rev;assert(gesture_bindings_parse("web:bindings 1 7,0,1,6,7,4,5,6",a,fx,rev));assert(fx[0]==5);assert(gesture_bindings_save(a,fx,rev)==BindingSave::Saved);assert(gesture_bindings_state().effects[0]==5);}
 }
}
''')
            exe=root/('effects.exe' if os.name=='nt' else 'effects')
            result=subprocess.run(['g++','-std=c++17','-pthread','-include',str(root/'catalog.h'),'-I'+str(root),'-I'+str(FIRMWARE/'src'),str(FIRMWARE/'src/Service/GestureBindings.cpp'),str(root/'harness.cpp'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            storage=root/'nvs';(storage/'wand-map').mkdir(parents=True);old=storage/'wand-map/letters-v1';old.write_bytes(bytes([5]*8));env=dict(os.environ,WAND_TEST_NVS=str(storage))
            for mode in ['migrate','save','load','fail','load','legacy']:
                subprocess.run([str(exe),mode],env=env,check=True,capture_output=True)
            self.assertEqual(old.read_bytes(),bytes([5]*8))
            self.assertEqual((storage/'wand-map3/letters-v1').stat().st_size,20)
