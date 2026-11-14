"""Real viewport initialization + production Cider drawing, poisoned stack, no LCD."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];S=Path(sys.argv[2]).resolve() if len(sys.argv)>2 else R
O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
def fn(text,name):
 m=re.search(r'(?m)^[\w *]+\b'+re.escape(name)+r'\([^;{}]*\)\s*\{',text);assert m,name
 e=m.end();d=1
 while d:d+=(text[e]=='{')-(text[e]=='}');e+=1
 return text[m.start():e]
s=(S/'apps-ipod/screens/playback/cider_screen.c').read_text()
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include "screens/playback/cider_model.h"
#define LCD_WIDTH 320
#define LCD_HEIGHT 240
#define HZ 100
#define SCREEN_MAIN 0
enum screen_type { main_screen = 0 };
#define FONT_UI 1
#define DRMODE_SOLID 0
#define FG_FALLBACK 1
#define BG_FALLBACK 0
#define LCDFN(x) x
#define LCD_RGBPACK(r,g,b) ((r)<<16|(g)<<8|(b))
#define VP_FLAG_VP_SET_BUFFER 1
struct frame_buffer_t {void *data;void *(*get_address_fn)(int,int);ptrdiff_t stride;size_t elems;};
struct viewport {int x,y,width,height,flags,font,drawmode;struct frame_buffer_t *buffer;unsigned fg_pattern,bg_pattern;};
static unsigned short cover[240*240],pixels[320*240];
static struct frame_buffer_t framebuffer_default={.data=pixels,.elems=320*240,.stride=320};
static struct viewport default_vp,*current;
static unsigned long current_tick;
INIT_VIEWPORT
static void align_flags(struct viewport*v){v->flags=0;}
#define set_default_align_flags align_flags
static int getuifont(void){return FONT_UI;}
struct screen {struct viewport*(*init_viewport)(struct viewport*);int lcdwidth,lcdheight;int(*getuifont)(void);};
static struct screen screens[]={{init_viewport,320,240,getuifont}};
FULLSCREEN
static int updates,bitmaps,lines;
static void lcd_set_viewport(struct viewport*v){current=init_viewport(v);assert(current->buffer==&framebuffer_default);}
static void lcd_set_background(unsigned c){(void)c;}
static void lcd_set_foreground(unsigned c){(void)c;}
static void lcd_clear_display(void){assert(current->buffer->data==pixels);}
static void lcd_bitmap(const unsigned short*p,int x,int y,int w,int h){assert(p==cover&&x==40&&!y&&w==240&&h==240);bitmaps++;}
static void lcd_fillrect(int x,int y,int w,int h){assert(x>=0&&y>=0&&w>=0&&h>=0&&x+w<=320&&y+h<=240);}
static void lcd_putsxy(int x,int y,const char*s){assert(x>=0&&x<320&&y>=0&&y<240&&strlen(s)<180);lines++;}
static void lcd_getstringsize(const char*s,int*w,int*h){*w=strlen(s)*7;*h=14;}
static void lcd_update(void){assert(current->buffer==&framebuffer_default);updates++;}
'''
pre=pre.replace('INIT_VIEWPORT',fn((S/'firmware/drivers/lcd-bitmap-common.c').read_text(),'LCDFN(init_viewport)'))
pre=pre.replace('FULLSCREEN',fn((S/'apps-ipod/draw/viewport.c').read_text(),'viewport_set_fullscreen'))
post=r'''
int main(void){struct cider_state s={.available=true,.have=true,.duration=100000,.position=12000,.rate=1000,.volume=50,.bg=0x123456};strcpy(s.title,"Title");strcpy(s.artist,"Artist");strcpy(s.album,"Album");for(int i=0;i<200;i++){current_tick=i*10;draw(&s,i%2,-1);}assert(updates==200&&bitmaps==100&&lines==800);puts("Production Cider drawing + real fullscreen/init_viewport, poisoned stack, bitmap/geometry bounds PASS");}
'''
(O/'viewport.c').write_text(pre+fn(s,'line')+fn(s,'draw')+post)
cmd=['cc','-std=gnu11','-Wall','-Wextra','-Werror','-ftrivial-auto-var-init=pattern','-fsanitize=address,undefined','-fno-pie','-no-pie','-I'+str(S/'apps-ipod'),'-I'+str(S/'firmware/export')]
subprocess.run(cmd+[str(O/'viewport.c'),str(S/'apps-ipod/screens/playback/cider_model.c'),str(S/'firmware/usbstack/podlink_wire.c'),'-o',str(O/'viewport')],check=True);subprocess.run([str(O/'viewport')],check=True)
