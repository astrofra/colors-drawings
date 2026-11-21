/* Colors! legacy DRW software playback. See docs/FORMAT.md. */
#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "brushes.h"

typedef struct {
    int w, h, version, active, control, brush;
    double radius, opacity, x, y, pressure;
    unsigned char color[3], *canvas, *base;
    float *coverage;
} Painter;

static void fail(const char *message) { fprintf(stderr, "replay: %s\n", message); exit(1); }
static uint32_t le32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static long number(const char *s, long lo, long hi) {
    char *end; errno=0; long n=strtol(s,&end,10);
    if(errno || !*s || *end || n<lo || n>hi) fail("invalid numeric argument");
    return n;
}
static double clamp(double v, double lo, double hi) { return fmin(hi,fmax(lo,v)); }
static void begin(Painter *p) {
    memcpy(p->base,p->canvas,(size_t)p->w*p->h*3);
    memset(p->coverage,0,(size_t)p->w*p->h*sizeof(float));
    p->active=1;
}
static double texture(int brush, double u, double v) {
    const unsigned char *a=brush ? brush_soft : brush_hard;
    double fx=clamp(u*256-0.5,0,255), fy=clamp(v*256-0.5,0,255);
    int x=(int)fx, y=(int)fy, x1=x<255?x+1:x, y1=y<255?y+1:y;
    double tx=fx-x,ty=fy-y;
    return ((a[y*256+x]*(1-tx)+a[y*256+x1]*tx)*(1-ty)
           +(a[y1*256+x]*(1-tx)+a[y1*256+x1]*tx)*ty)/255.0;
}
static void dab(Painter *p,double x,double y,double pressure) {
    double radius=p->radius, alpha=p->opacity;
    if(p->control==2 || p->control==3) radius*=pressure;
    if(p->control==1 || p->control==3) alpha*=pressure;
    radius=fmax(radius,0.25);
    int left=(int)fmax(0,floor(x-radius)),right=(int)fmin(p->w-1,ceil(x+radius));
    int top=(int)fmax(0,floor(y-radius)),bottom=(int)fmin(p->h-1,ceil(y+radius));
    for(int iy=top;iy<=bottom;iy++) for(int ix=left;ix<=right;ix++) {
        double u=(ix+0.5-x)/(2*radius)+0.5,v=(iy+0.5-y)/(2*radius)+0.5;
        if(u<0 || u>1 || v<0 || v>1) continue;
        double coverage=texture(p->brush,u,v)*alpha;
        size_t pos=(size_t)iy*p->w+ix;
        /* A stroke is a coverage mask over the canvas at pen-down. Repeated
           samples must not darken the same pixel as independent strokes. */
        if(coverage<=p->coverage[pos]) continue;
        p->coverage[pos]=(float)coverage;
        for(int k=0;k<3;k++) p->canvas[pos*3+k]=(unsigned char)lround(
            p->base[pos*3+k]*(1-coverage)+p->color[k]*coverage);
    }
}
static void point(Painter *p,double x,double y,double pressure) {
    if(!p->active) { begin(p); dab(p,x,y,pressure); }
    else {
        double dx=x-p->x,dy=y-p->y,dist=hypot(dx,dy);
        /* Fixed spacing tied to brush radius; interpolate pressure too. */
        double r=p->radius;
        if(p->control==2 || p->control==3) r*=fmin(pressure,p->pressure);
        double spacing=fmax(0.5,r*0.15);
        int steps=(int)ceil(dist/spacing); if(steps<1) steps=1;
        for(int i=1;i<=steps;i++) { double t=(double)i/steps;
            dab(p,p->x+dx*t,p->y+dy*t,p->pressure+(pressure-p->pressure)*t);
        }
    }
    p->x=x;p->y=y;p->pressure=pressure;
}
static void flip(Painter *p,int horizontal,int vertical) {
    p->active=0;
    memcpy(p->base,p->canvas,(size_t)p->w*p->h*3);
    for(int y=0;y<p->h;y++) for(int x=0;x<p->w;x++) {
        size_t src=((size_t)(vertical?p->h-1-y:y)*p->w+(horizontal?p->w-1-x:x))*3;
        memcpy(p->canvas+((size_t)y*p->w+x)*3,p->base+src,3);
    }
}
static void command(Painter *p,uint32_t c) {
    switch(c&3) {
    case 0: point(p,(((int)(c>>10)&2047)-512)*p->w/1024.0,
                    (((int)(c>>21)&2047)-512)*p->h/1024.0,((c>>2)&255)/255.0);break;
    case 1: p->active=0; break; /* NDS stores pen-up pressure in bits 2..9. */
    case 2:
        p->active=0;
        if(c & 0x0c000000) flip(p,!!(c&0x04000000),!!(c&0x08000000));
        else {p->color[0]=(c>>18)&255;p->color[1]=(c>>10)&255;p->color[2]=(c>>2)&255;}
        break;
    case 3:
        p->active=0;p->radius=((c>>2)&65535)*p->w/65535.0;
        if(p->version==1002) {
            p->control=(c>>18)&3;p->brush=(c>>20)&3;p->opacity=((c>>22)&255)/255.0;
        } else {
            p->control=(c>>18)&7;p->brush=(c>>21)&7;p->opacity=(c>>24)/255.0;
        }
        break;
    }
}
static void frame(Painter *p) {
    size_t bytes=(size_t)p->w*p->h*3;
    if(fwrite(p->canvas,1,bytes,stdout)!=bytes) fail("cannot write RGB frame (encoder closed pipe?)");
}
int main(int argc,char **argv) {
    if(argc!=6 && argc!=7) {
        fprintf(stderr,"Usage: %s input.drw width height frames hold_frames [final.ppm]\n"
                       "Writes RGB24 frames to stdout. frames >= 2; hold_frames >= 0.\n",argv[0]);return 2;
    }
    int w=(int)number(argv[2],2,8192),h=(int)number(argv[3],2,8192);
    long frames=number(argv[4],2,10000000),hold=number(argv[5],0,10000000);
    FILE *f=fopen(argv[1],"rb");if(!f) fail("cannot open DRW");
    if(fseek(f,0,SEEK_END)) fail("cannot seek DRW");
    long length=ftell(f);if(length<64 || length>256*1024*1024) fail("invalid DRW length");
    rewind(f);unsigned char *data=malloc((size_t)length);if(!data) fail("out of memory");
    if(fread(data,1,(size_t)length,f)!=(size_t)length) fail("cannot read DRW");fclose(f);
    if(le32(data)!=0x436f6c21) fail("invalid DRW magic");
    int version=(int)le32(data+4);
    if(version!=1002 && version!=1071) fail("unsupported DRW version (supported: 1002, 1071)");
    uint32_t count=le32(data+60);
    if((uint64_t)count*4+64!=(uint64_t)length) fail("truncated DRW or unexpected trailing data");
    /* Validate every operation BEFORE sending frames to an encoder. */
    for(uint32_t i=0;i<count;i++) {
        uint32_t c=le32(data+64+(size_t)i*4);
        if((c&3)==3) {
            unsigned brush=version==1002?(c>>20)&3:(c>>21)&7;
            unsigned control=version==1002?(c>>18)&3:(c>>18)&7;
            if(brush>1 || control>3) fail("unsupported brush or pressure control");
        }
        if((c&3)==1 && ((version==1002 && c>>10) || (version==1071 && c!=1)))
            fail("unsupported pen-up/layer operation");
        if((c&3)==2 && c>>28) fail("unsupported user/color flags");
    }
    size_t pixels=(size_t)w*h;
    Painter p={0};p.w=w;p.h=h;p.version=version;p.radius=10;p.opacity=1;p.control=1;
    p.canvas=malloc(pixels*3);p.base=malloc(pixels*3);p.coverage=calloc(pixels,sizeof(float));
    if(!p.canvas || !p.base || !p.coverage) fail("out of memory");memset(p.canvas,255,pixels*3);
    uint32_t index=0;
    for(long n=0;n<frames;n++) {
        uint32_t target=(uint32_t)((uint64_t)n*count/(uint64_t)(frames-1));
        while(index<target) {command(&p,le32(data+64+(size_t)index*4));index++;}
        frame(&p);
    }
    for(long n=0;n<hold;n++) frame(&p);
    if(argc==7) {
        FILE *out=fopen(argv[6],"wb");if(!out) fail("cannot open PPM");
        fprintf(out,"P6\n%d %d\n255\n",w,h);
        if(fwrite(p.canvas,1,pixels*3,out)!=pixels*3 || fclose(out)) fail("cannot write PPM");
    }
    if(fflush(stdout)) fail("cannot flush RGB stream");
    fprintf(stderr,"replay: %u commands, %ld frames, %dx%d, DRW %d\n",count,frames+hold,w,h,version);
    free(data);free(p.canvas);free(p.base);free(p.coverage);return 0;
}
