"""Generative cinematic hero engine for the website studio.

Every generated site gets a LIVING, animated WebGL hero - the motion.ai / igloo.inc kind of feel - instead
of a static image or one repeated gradient. There are several visually distinct archetypes (flowing aurora,
silk ribbons, volumetric light rays, ink bloom, topographic contours, drifting bokeh, cinematic beams); the
business name seeds which one a site gets, and the trade biases the choice so the look fits the business
(light rays for a spa, contours for a property firm, beams for a gym). Each is recoloured to the site's
palette and reacts subtly to the pointer, so two same-trade sites never feel the same and every hero moves.

Pure WebGL fragment shaders on a single full-screen quad - no heavy 3D libraries, no build step, works on the
shared Tailscale link and offline. Falls back to a CSS gradient if WebGL is unavailable.
"""
import hashlib

# business words -> which hero archetypes suit that trade (first match wins; order = preference)
_TRADE_POOLS = [
    (("spa", "wellness", "massage", "beauty", "aesthetic", "lash", "salon", "nail", "facial"),
     ["rays", "silk", "bloom"]),
    (("barber", "tattoo", "ink"), ["bloom", "beams", "aurora"]),
    (("gym", "fitness", "crossfit", "box", "sport", "athlet", "train"), ["beams", "aurora", "rays"]),
    (("property", "estate", "real estate", "letting", "architect", "construct", "build", "renovat", "plumb",
      "electric", "landscap", "roofing"), ["contour", "beams", "silk"]),
    (("tech", "software", "it ", "digital", "cyber", "data", "cloud", "network", "saas", "app", "dev", "ai "),
     ["bokeh", "aurora", "beams"]),
    (("finance", "account", "legal", "law", "consult", "corporate", "advis", "insur", "bank", "invest"),
     ["silk", "aurora", "contour"]),
    (("restaurant", "cafe", "coffee", "bar", "pub", "food", "grill", "bistro", "bakery", "kitchen"),
     ["bloom", "aurora", "rays"]),
    (("hotel", "lodge", "guest", "travel", "tour", "event", "wedding", "hospitality"),
     ["rays", "bokeh", "silk"]),
    (("jewel", "watch", "luxury", "fashion", "boutique", "auto", "car", "detail", "motor"),
     ["silk", "bokeh", "bloom"]),
    (("solar", "energy", "green", "farm", "agri", "garden", "nursery", "clean", "eco"),
     ["contour", "aurora", "rays"]),
    (("photo", "studio", "creative", "design", "media", "agency", "market", "brand", "film", "music", "art"),
     ["aurora", "bloom", "bokeh"]),
]
_ALL = ["aurora", "silk", "rays", "bloom", "contour", "bokeh", "beams"]


def _seed(business=""):
    s = str(business or "").strip().lower()
    if not s:
        return 0
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest()[:8], 16)


def pick(category="", business=""):
    """Pick a hero archetype: trade-appropriate, seeded by the business so two same-trade sites differ."""
    c = (str(category or "") + " " + str(business or "")).lower()
    pool = None
    for words, p in _TRADE_POOLS:
        if any(w in c for w in words):
            pool = p
            break
    if not pool:
        pool = _ALL
    return pool[_seed(business) % len(pool)]


# ---- shared GLSL (simplex noise + fbm), used by the effects that need it -------------------------------
_PRELUDE = r"""
vec3 md(vec3 x){return x-floor(x*(1./289.))*289.;}
vec2 md2(vec2 x){return x-floor(x*(1./289.))*289.;}
vec3 pm(vec3 x){return md(((x*34.)+1.)*x);}
float sn(vec2 v){const vec4 C=vec4(.211324865,.366025403,-.577350269,.024390243);
 vec2 i=floor(v+dot(v,C.yy));vec2 x0=v-i+dot(i,C.xx);vec2 i1=(x0.x>x0.y)?vec2(1.,0.):vec2(0.,1.);
 vec4 x12=x0.xyxy+C.xxzz;x12.xy-=i1;i=md2(i);
 vec3 p=pm(pm(i.y+vec3(0.,i1.y,1.))+i.x+vec3(0.,i1.x,1.));
 vec3 m=max(.5-vec3(dot(x0,x0),dot(x12.xy,x12.xy),dot(x12.zw,x12.zw)),0.);m=m*m;m=m*m;
 vec3 x=2.*fract(p*C.www)-1.;vec3 h=abs(x)-.5;vec3 ox=floor(x+.5);vec3 a0=x-ox;
 m*=1.79284291-.85373472*(a0*a0+h*h);vec3 g;g.x=a0.x*x0.x+h.x*x0.y;g.yz=a0.yz*x12.xz+h.yz*x12.yw;return 130.*dot(m,g);}
float fb(vec2 p){float s=0.,a=.5;for(int i=0;i<5;i++){s+=a*sn(p);p*=2.02;a*=.5;}return s;}
"""

# ---- effect bodies (each sets vec3 col; uv in 0..1, p is aspect-corrected, tt = slow time) --------------
_EFFECTS = {
# flowing domain-warped aurora gradient (the classic premium "living gradient")
"aurora": r"""
 float n1=fb(p*1.6+vec2(tt,tt*.6));
 float n2=fb(p*2.3-vec2(tt*.8,tt)+n1*.7);
 float n3=fb(p*1.1+vec2(-tt*.5,tt*.9)+n2*.5);
 col=c0;col=mix(col,c1,smoothstep(-.2,.9,n1));col=mix(col,c2,smoothstep(.0,1.,n2)*.85);
 col=mix(col,c3,smoothstep(.3,1.1,n3)*.6);
 float grd=smoothstep(1.15,-.15,uv.y);col*=(.78+.42*grd);
""",
# smooth flowing silk ribbons - elegant, luxe
"silk": r"""
 float w=0.;
 for(int i=0;i<4;i++){float fi=float(i);
   float band=sin((p.x*2.2)+ (p.y*3.0) + tt*1.4 + fi*1.7 + fb(p*1.3+fi+tt*.3)*2.4);
   w+=smoothstep(.0,1.6,band)* (0.5-fi*0.08);}
 w/=3.0;
 col=mix(c0,c1,smoothstep(.1,.95,w));
 col=mix(col,c2,smoothstep(.45,1.1,w)*.8);
 col=mix(col,c3,pow(max(w-.6,0.),2.0)*1.4);
 col*= .82+.3*smoothstep(1.2,-.1,uv.y);
""",
# volumetric light rays from a slowly drifting source - spa / luxury / hospitality
"rays": r"""
 vec2 src=vec2(.5+.25*sin(tt*.5),1.15);
 vec2 d=uv-src; float ang=atan(d.y,d.x); float rad=length(d);
 float rays=0.;
 for(int i=0;i<3;i++){float fi=float(i);
   rays+=smoothstep(.2,1.,sin(ang*(16.+fi*7.)+tt*(.6+fi*.2)+fb(p*2.+fi)*3.))*(.4-fi*.1);}
 float fall=smoothstep(1.3,.0,rad);
 float haze=fb(p*2.2+vec2(0.,tt));
 col=c0;
 col=mix(col,c1,fall*(.35+.4*haze));
 col=mix(col,c3,rays*fall*1.1);
 col=mix(col,c2,smoothstep(.6,1.2,haze)*fall*.5);
 col*= .8+.35*smoothstep(1.2,-.1,uv.y);
""",
# organic ink / smoke bloom pulsing from centre - creative / food / barber
"bloom": r"""
 vec2 q=p; q += vec2(fb(p*1.5+tt*.3),fb(p*1.5-tt*.25))*.6;
 float r=length(q-vec2(.5*r_aspect,.5));
 float bloom=fb(q*2.4+vec2(tt*.4,-tt*.3));
 float core=smoothstep(.9,.0,r)* (.6+.5*bloom);
 col=c0;
 col=mix(col,c2,smoothstep(.1,.9,bloom)*.7);
 col=mix(col,c1,core);
 col=mix(col,c3,pow(core,2.2)*1.3);
 col*= .8+.3*smoothstep(1.2,-.1,uv.y);
""",
# topographic moving contour lines - property / construction / outdoors / tech-maps
"contour": r"""
 float h=fb(p*1.7+vec2(tt*.4,tt*.2));
 h+=.5*fb(p*3.4-vec2(tt*.3,0.));
 float lines=abs(fract(h*5.0)-.5);
 float edge=smoothstep(.06,.0,lines);
 float fld=smoothstep(-1.,1.,h);
 col=mix(c0,c1,fld*.6);
 col=mix(col,c2,fld*fld*.5);
 col+=c3*edge*1.1;
 col*= .82+.3*smoothstep(1.2,-.1,uv.y);
""",
# drifting soft glowing bokeh orbs - tech / events / modern
"bokeh": r"""
 col=c0;
 for(int i=0;i<10;i++){float fi=float(i);
   float sp=.08+ .12*fract(fi*.137);
   vec2 bp=vec2(fract(sin(fi*12.9)*43758.5)+ .15*sin(tt*sp+fi),
               fract(sin(fi*78.2)*12345.6)+ .12*cos(tt*sp*.8+fi*1.3));
   bp.x*=r_aspect;
   float rr=length(p-bp);
   float glow=smoothstep(.16+.06*fract(fi*.7),.0,rr);
   vec3 tint=mix(c1,c3, fract(fi*.31));
   col+=tint*glow*(.35+.2*sin(tt+fi));}
 col=mix(col,c2,fb(p*1.4+tt*.2)*.25);
 col*= .8+.35*smoothstep(1.2,-.1,uv.y);
""",
# cinematic horizontal light beams sweeping - gym / sport / bold brands
"beams": r"""
 float b=0.;
 for(int i=0;i<5;i++){float fi=float(i);
   float yy=fract(fi*.23+.1)+ .08*sin(tt*.5+fi);
   float band=smoothstep(.09,.0,abs(uv.y-yy)-.01);
   float sweep=smoothstep(.0,1.,sin(uv.x*3.0 - tt*(1.2+fi*.3) + fb(p*1.5+fi)*2.));
   b+=band*sweep*(.5-fi*.06);}
 float haze=fb(p*2.+vec2(tt*.3,0.));
 col=c0;
 col=mix(col,c2,haze*.3);
 col+= mix(c1,c3,uv.x)*b*1.2;
 col*= .82+.3*smoothstep(1.2,-.1,uv.y);
"""
}


def engine(effect="aurora"):
    """Return the full <script> block that draws the chosen animated hero into #gl, palette-driven.
    Uses __C_*__ tokens so web.render() fills the site's colours, exactly like the other templates."""
    body = _EFFECTS.get(effect, _EFFECTS["aurora"])
    return (
        "<script>(function(){\n"
        "var cv=document.getElementById('gl');if(!cv)return;var gl=cv.getContext('webgl')||cv.getContext('experimental-webgl');\n"
        "if(!gl){cv.style.background='linear-gradient(135deg,__C_BG__,__C_ACCENT__,__C_ACCENT2__)';return;}\n"
        "function hx(h){h=h.replace('#','');if(h.length===3)h=h.split('').map(function(c){return c+c;}).join('');"
        "return[parseInt(h.slice(0,2),16)/255,parseInt(h.slice(2,4),16)/255,parseInt(h.slice(4,6),16)/255];}\n"
        "var C=[hx('__C_BG__'),hx('__C_ACCENT__'),hx('__C_ACCENT2__'),hx('__C_GLOW__')];\n"
        "var vs='attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';\n"
        "var fs='precision highp float;uniform vec2 r;uniform float t;uniform vec2 mo;uniform vec3 c0,c1,c2,c3;'+\n"
        " " + _js(_PRELUDE) + "+\n"
        " 'void main(){float r_aspect=r.x/r.y;vec2 uv=gl_FragCoord.xy/r;vec2 p=uv;p.x*=r_aspect;'+\n"
        " 'p+=(mo-0.5)*0.12;float tt=t*0.045;vec3 col=c0;'+\n"
        " " + _js(body) + "+\n"
        " 'gl_FragColor=vec4(col,1.);}';\n"
        "function sh(ty,s){var o=gl.createShader(ty);gl.shaderSource(o,s);gl.compileShader(o);return o;}\n"
        "var pr=gl.createProgram();gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs));"
        "gl.linkProgram(pr);\n"
        "if(!gl.getProgramParameter(pr,gl.LINK_STATUS)){cv.style.background='linear-gradient(135deg,__C_BG__,__C_ACCENT__,__C_ACCENT2__)';return;}\n"
        "gl.useProgram(pr);\n"
        "var bf=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,bf);"
        "gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW);\n"
        "var lp=gl.getAttribLocation(pr,'p');gl.enableVertexAttribArray(lp);gl.vertexAttribPointer(lp,2,gl.FLOAT,false,0,0);\n"
        "var uR=gl.getUniformLocation(pr,'r'),uT=gl.getUniformLocation(pr,'t'),uM=gl.getUniformLocation(pr,'mo');\n"
        "gl.uniform3fv(gl.getUniformLocation(pr,'c0'),C[0]);gl.uniform3fv(gl.getUniformLocation(pr,'c1'),C[1]);"
        "gl.uniform3fv(gl.getUniformLocation(pr,'c2'),C[2]);gl.uniform3fv(gl.getUniformLocation(pr,'c3'),C[3]);\n"
        "var mo=[0.5,0.5],mt=[0.5,0.5];\n"
        "addEventListener('pointermove',function(e){mt=[e.clientX/innerWidth,e.clientY/innerHeight];});\n"
        "function sz(){var d=Math.min(devicePixelRatio||1,2);cv.width=innerWidth*d;cv.height=Math.max(innerHeight,1)*d;"
        "gl.viewport(0,0,cv.width,cv.height);gl.uniform2f(uR,cv.width,cv.height);}\n"
        "addEventListener('resize',sz);sz();var st=performance.now();\n"
        "(function loop(n){mo[0]+=(mt[0]-mo[0])*.05;mo[1]+=(mt[1]-mo[1])*.05;gl.uniform2f(uM,mo[0],mo[1]);"
        "gl.uniform1f(uT,(n-st)/1000);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);requestAnimationFrame(loop);})(st);\n"
        "})();</script>"
    )


def _js(glsl):
    """Turn a multi-line GLSL block into a single JS string literal (one quoted line per source line)."""
    lines = [ln.strip() for ln in glsl.strip().split("\n") if ln.strip()]
    return "+\n ".join("'" + ln.replace("\\", "\\\\").replace("'", "\\'") + "'" for ln in lines)
