"""Actual preset renderer: finite lifetime, 39-pixel bounds and preview clearing."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import find_template

FASTLED=r'''
#pragma once
#include <stdint.h>
struct CHSV{uint8_t h,s,v;CHSV(uint8_t a,uint8_t b,uint8_t c):h(a),s(b),v(c){}};
struct CRGB{
 uint8_t r=0,g=0,b=0;
 CRGB()=default;CRGB(uint32_t c):r(c>>16),g(c>>8),b(c){}
 CRGB(uint8_t x,uint8_t y,uint8_t z):r(x),g(y),b(z){}
 CRGB(CHSV c):r(c.v),g(c.v/2),b(c.h%32){}
 operator uint32_t()const{return (uint32_t(r)<<16)|(uint32_t(g)<<8)|b;}
 void nscale8(uint8_t n){r=(r*n)>>8;g=(g*n)>>8;b=(b*n)>>8;}
 CRGB&operator+=(const CRGB&c){r=r+c.r>255?255:r+c.r;g=g+c.g>255?255:g+c.g;b=b+c.b>255?255:b+c.b;return *this;}
 bool operator==(const CRGB&c)const{return r==c.r&&g==c.g&&b==c.b;}
 static const CRGB White,Black;
};
inline const CRGB CRGB::White{0xffffff},CRGB::Black{0};
'''

@unittest.skipUnless(shutil.which('g++'),'requires native C++ compiler')
class GestureEffectTests(unittest.TestCase):
    def test_all_presets_render_finish_and_respect_39_led_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'HAL').mkdir()
            (root/'Arduino.h').write_text('#pragma once\n#include <stdint.h>\nextern uint32_t clockMs;inline uint32_t millis(){return clockMs;}\n')
            (root/'FastLED.h').write_text(FASTLED)
            (root/'HAL/HAL.h').write_text('#pragma once\nclass AnimationBase;namespace HAL{void ws2812_start_fx(AnimationBase*);}\n')
            (root/'test.cpp').write_text(r'''
#include "HAL/WS2812_Animation/AnimGestureFx.hpp"
#include "HAL/WS2812_Animation/AnimFlow.hpp"
#include "Service/GestureEffects.h"
#include <cassert>
#include <set>
uint32_t clockMs=0;AnimationBase*current=nullptr;
namespace HAL{void ws2812_start_fx(AnimationBase*a){delete current;current=a;if(a)a->init();}}
int main(){
 assert(kGestureEffectChoiceCount==27);std::set<int>colors;
 for(int id=1;id<=25;++id){
  clockMs=0;gesture_effect_play(id);assert(current);bool visible=false;
  for(int t=0;t<=1900;t+=10){
   CRGB buffer[41];buffer[0]=CRGB(1,2,3);buffer[40]=CRGB(4,5,6);clockMs=t;current->update(buffer+1,39);
   assert(buffer[0]==CRGB(1,2,3)&&buffer[40]==CRGB(4,5,6));
   for(int i=1;i<=39;++i)if(!(buffer[i]==CRGB::Black))visible=true;
   if(id<=8&&t==80){auto c=buffer[18];colors.insert((c.r<<16)|(c.g<<8)|c.b);}
  }
  assert(visible&&current->finished());
 }
 assert(colors.size()==8);
 // Match the original renderer frame-by-frame, including the 0.30s lifetime.
 const uint32_t rgb[]={0xFF2020,0x20FF50,0xFFD000,0xA84CFF,0xFF60B5,0x3080FF,0x28E8D8,0xFFFFFF};
 for(int id=1;id<=8;++id){
  clockMs=0;gesture_effect_play(id);AnimFlow original{CRGB(rgb[id-1])};original.init();int frames=0;
  while(!original.finished()){
   CRGB actual[39],expected[39];clockMs=frames*10;
   current->update(actual,39);original.update(expected,39);
   for(int i=0;i<39;++i)assert(actual[i]==expected[i]);
   assert(current->finished()==original.finished());assert(++frames<=30);
  }
  assert(frames==30);
 }

 auto old=current;gesture_effect_play(-2);assert(current==old);gesture_effect_play(26);assert(current==old);
 gesture_effect_play(-1);assert(current==old);gesture_effect_play(0);assert(!current);
 AnimGestureFx empty(1,CRGB::White);empty.init();empty.update(nullptr,0);assert(empty.finished());
}
''')
            exe=root/('effects.exe' if os.name=='nt' else 'effects');firmware=find_template()
            build=subprocess.run(['g++','-std=c++17','-I'+str(root),'-I'+str(firmware/'src'),str(firmware/'src/Service/GestureEffects.cpp'),str(root/'test.cpp'),'-o',str(exe)],capture_output=True,text=True)
            self.assertEqual(build.returncode,0,build.stderr);subprocess.run([str(exe)],check=True,capture_output=True)
