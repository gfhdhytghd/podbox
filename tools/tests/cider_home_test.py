"""Production Home insertion: persisted order, Music hidden, automatic spoken rows."""
from pathlib import Path
import re,subprocess,sys
R=Path(__file__).resolve().parents[2];O=Path(sys.argv[1]).resolve();O.mkdir(parents=True,exist_ok=True)
s=(R/'apps-ipod/root_menu.c').read_text()
assert re.search(r'MENUITEM_RETURNVALUE\(cider_item,.*?item_callback, Icon_Playlist\)',s,re.S)
m=re.search(r'static (?:void|bool) root_menu_place_cider\(void\)\s*\{',s);a=m.start();e=m.end();d=1
while d:d+=(s[e]=='{')-(s[e]=='}');e+=1
fn=s[a:e]
assert 'root_menu_set_count(count);\n    root_menu_place_cider();' in s
pre=r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#define HAVE_USB_PODLINK
#define MAX_MENU_ITEMS 16
#define MENU_COUNT_SHIFT 0
#define MENU_COUNT_MASK 255
#define MENU_GET_COUNT(f) ((f)&255)
#define MENU_ITEM_COUNT(n) (n)
struct menu_item_ex{unsigned int flags;};
static struct menu_item_ex root_menu_,db_browser,cider_item,shortcut_menu,others[8];
static struct menu_item_ex*root_menu__[16];
#define ACTION_REQUEST_MENUITEM 1
#define ACTION_EXIT_MENUITEM 2
static bool available;
static struct menu_item_ex*hidden;
static bool cider_available(void){return available;}
static int item_callback(int a,const struct menu_item_ex*i,void*l){(void)l;return i==hidden?ACTION_EXIT_MENUITEM:a;}
static int shortcut_menu_callback(int a,const struct menu_item_ex*i,void*l){return item_callback(a,i,l);}

'''
post=r'''
int main(void){
for(unsigned int mask=0;mask<256;mask++){
struct menu_item_ex*base[16];unsigned int count=0;
for(int i=0;i<8;i++)if(mask&(1u<<i))base[count++]=&others[i];
for(unsigned int where=0;where<=count;where++){
memcpy(root_menu__,base,count*sizeof(base[0]));
memmove(root_menu__+where+1,root_menu__+where,(count-where)*sizeof(base[0]));root_menu__[where]=&db_browser;unsigned int initial=count+1;
for(int hide=0;hide<2;hide++){
struct menu_item_ex*saved[16];memcpy(saved,root_menu__,initial*sizeof(base[0]));
hidden=hide?root_menu__[0]:NULL;root_menu_.flags=initial;available=true;
assert(root_menu_place_cider());unsigned int n=MENU_GET_COUNT(root_menu_.flags),visible=0;
for(unsigned int j=0;j<n;j++)if(root_menu__[j]!=hidden){if(root_menu__[j]==&cider_item)assert(visible==(initial>(unsigned)hide?1u:0u));visible++;}
assert(n==initial+1);assert(!root_menu_place_cider());
available=false;assert(root_menu_place_cider());assert(MENU_GET_COUNT(root_menu_.flags)==initial);
assert(!memcmp(saved,root_menu__,initial*sizeof(base[0])));assert(!root_menu_place_cider());
memcpy(root_menu__,saved,initial*sizeof(base[0]));
}
}
}
puts("Home: availability transitions, second visible row, preserved order, hidden first row, unique Cider PASS");}
'''
(O/'home.c').write_text(pre+fn+post)
subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-g','-fsanitize=address,undefined','-fno-pie','-no-pie',str(O/'home.c'),'-o',str(O/'home')],check=True);subprocess.run([str(O/'home')],check=True)
