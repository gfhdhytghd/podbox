/* Production Cider cache/state/chunks; offline, no player or sockets. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "screens/playback/cider_model.h"
#include "podlink.h"
static struct cider_cache cache;
static struct cider_transfer transfer;
static unsigned char art[CIDER_ART_MAX], chunk[12288];
static void put32(unsigned char *p, uint32_t v)
{ for (int i = 0; i < 4; i++) p[i] = v >> (8*i); }
static unsigned int make_chunk(unsigned int offset, unsigned int count,
                                unsigned int total)
{
    memcpy(chunk, "CAR1", 4);put32(chunk+4,7);put32(chunk+8,1);
    put32(chunk+12,offset);put32(chunk+16,total);
    put32(chunk+20,podlink_crc32(art,total));
    memcpy(chunk+28,art+offset,count);
    put32(chunk+24,podlink_crc32(chunk+28,count));return 28+count;
}
static void make_art(unsigned int dim, bool columns)
{
    memset(art,0,sizeof art);put32(art,8u<<16|0x5441);
    put32(art+4,dim<<16|dim);put32(art+8,columns);
}
int main(void)
{
    struct cider_state s={0};
    const char *available="CA\t7\t1\t";
    assert(cider_state_apply(&s,available,strlen(available),100)==1);
    const char *now="CN\t7\t1\t4000\t100000\t12000\t1000\t50\t0\t0\tsong\t-\t123456\tTitle\tArtist\tAlbum";
    assert(cider_state_apply(&s,now,strlen(now),100)==1);
    assert(cider_position(&s,250,100)==13500);
    assert(!cider_state_apply(&s,now,strlen(now),250));
    s.rate=0;assert(cider_position(&s,1000,100)==12000);
    s.rate=1000;s.received=UINT32_MAX-10;
    assert(cider_position(&s,89,100)==13000);
    now="CN\t7\t2\t4000\t100000\t12000\t1000\t101\t0\t0\tsong\t-\t123456\tTitle\tArtist\tAlbum";
    assert(!cider_state_apply(&s,now,strlen(now),100));
    char key[65];memset(key,'a',64);key[64]=0;
    make_art(128,true);unsigned int total=12+128*128*2;
    transfer.active=true;transfer.generation=7;transfer.request=1;
    strcpy(transfer.key,key);strcpy(transfer.tier,"M");
    assert(cider_chunk(&transfer,chunk,1)==-1);
    unsigned int len=make_chunk(0,12260,total);
    chunk[28]^=1;assert(cider_chunk(&transfer,chunk,len)==-1);chunk[28]^=1;
    assert(cider_chunk(&transfer,chunk,len)==0);assert(transfer.received==12260);
    /* Reissue one whole-cover request at the retained offset, no chunk ACK. */
    for(unsigned int off=transfer.received;off<total;)
    {
        unsigned int bytes=total-off<12260?total-off:12260;
        len=make_chunk(off,bytes,total);int done=cider_chunk(&transfer,chunk,len);
        off+=bytes;assert(done==(off==total));
    }
    assert(!transfer.active&&transfer.received==total);
    assert(cider_cache_put(&cache,key,"M",transfer.data,total,true));
    assert(cider_cache_find(&cache,key,"M")>=0);
    assert(cider_cache_find(&cache,key,"L")==-1);
    for(int i=0;i<80;i++)
    {
        snprintf(key,sizeof key,"%064x",i);
        make_art(128,true);
        assert(cider_cache_put(&cache,key,"M",art,total,false));
        assert(cache.bytes<=CIDER_RAM_BYTES);
    }
    snprintf(key,sizeof key,"%064x",0);
    assert(cider_cache_find(&cache,key,"M")==-1);
    snprintf(key,sizeof key,"%064x",79);
    assert(cider_cache_find(&cache,key,"M")>=0);
    int window[9];assert(cider_window(10,20,window)==9);
    assert(window[0]==10&&window[1]==11&&window[2]==9);
    int slots[9];for(int i=0;i<9;i++)slots[i]=-1;
    for(int center=0;center<2000;center++)
    {
        int n=cider_window(center,center%2?20:-20,window);
        for(int j=0;j<n;j++)
        {
            int slot=cider_slide_slot(slots,center,window[j]);assert(slot>=0);
            if(slots[slot]>=0&&slots[slot]!=window[j])
                assert(abs(slots[slot]-center)>abs(window[j]-center));
            slots[slot]=window[j];
        }
        assert(cider_slide_slot(slots,center,center+5)<0);
        for(int j=0;j<9;j++)for(int k=j+1;k<9;k++)assert(slots[j]<0||slots[j]!=slots[k]);
    }
    available="CA\t8\t0\tOffline";
    assert(cider_state_apply(&s,available,strlen(available),200)==1);
    assert(!s.have&&!s.available&&s.generation==8);
    assert(cider_cache_find(&cache,key,"M")>=0);
    printf("Cider production state/LRU/CRC/resume/generation/window PASS; cache=%zu transfer=%zu\n",sizeof cache,sizeof transfer);
    return 0;
}
