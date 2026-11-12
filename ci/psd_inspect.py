#!/usr/bin/env python3
"""Lists the layer records of a PSD file (blend mode, clipping, channels,
blending ranges = Blend If, additional info keys = adjustment layers...).
With --check-smoke it also checks the file written by ci/smoke/ps_smoke.py
and exits with 1 when something Photoshop needs is missing.

Usage: psd_inspect.py file.psd [--check-smoke]"""
import struct, sys
def u8(b,o): return b[o], o+1
def u16(b,o): return struct.unpack_from(">H",b,o)[0], o+2
def u32(b,o): return struct.unpack_from(">I",b,o)[0], o+4
def dump(path, verbose=True):
    b=open(path,"rb").read()
    assert b[:4]==b"8BPS"
    ver=struct.unpack_from(">H",b,4)[0]
    nch=struct.unpack_from(">H",b,12)[0]
    o=26
    n,o=u32(b,o); o+=n          # color mode
    n,o=u32(b,o); o+=n          # resources
    lm,o=u32(b,o) if ver==1 else (struct.unpack_from(">Q",b,o)[0],o+8)
    li,o=u32(b,o) if ver==1 else (struct.unpack_from(">Q",b,o)[0],o+8)
    cnt=struct.unpack_from(">h",b,o)[0]; o+=2
    out=[]
    for i in range(abs(cnt)):
        t,l,bt,r,c=struct.unpack_from(">iiiiH",b,o); o+=18
        chans=[]
        for k in range(c):
            cid=struct.unpack_from(">h",b,o)[0]; o+=2
            ln,o=(u32(b,o) if ver==1 else (struct.unpack_from(">Q",b,o)[0],o+8)); chans.append(cid)
        sig=b[o:o+4]; mode=b[o+4:o+8]; op=b[o+8]; clip=b[o+9]; flags=b[o+10]; o+=12
        ex,o=u32(b,o); end=o+ex
        mk,o=u32(b,o); o+=mk
        br,o=u32(b,o); brd=b[o:o+br]; o+=br
        nl=b[o]; name=b[o+1:o+1+nl].decode("latin1"); o+=((nl+1+3)&~3)
        keys=[]
        while o+12<=end:
            s=b[o:o+4]; k=b[o+4:o+8].decode("latin1"); o+=8
            ln,o=u32(b,o); keys.append((k,ln)); o+=ln
            if ln%2 and False: o+=1
        o=end
        out.append((name,mode.decode(),clip,chans,br,brd.hex(),keys))
    return nch,out

EXPECT_ADJ = {"Levels": "levl", "Curves": "curv", "Hue/Saturation": "hue2",
              "Color Balance": "blnc", "Selective Color": "selc", "Invert": "nvrt",
              "Posterize": "post", "Threshold": "thrs"}


def check_smoke(layers):
    bad = []
    by = {l[0]: l for l in layers}
    bi = by.get("BlendIf red")
    if bi is None:
        bad.append("no 'BlendIf red' layer")
    else:
        br = bytes.fromhex(bi[5])
        if bi[4] < 16 or br[4:8] != bytes((128, 128, 255, 255)):
            bad.append("Blend If not written: blending ranges %d bytes %s" % (bi[4], bi[5]))
    cl = by.get("Clipped blue")
    if cl is None or cl[2] != 1:
        bad.append("clipping flag missing on 'Clipped blue'")
    base = by.get("Base square")
    if base is not None and base[2] != 0:
        bad.append("base must not be clipped")
    gb = by.get("Group BlendIf")
    if gb is None:
        bad.append("no 'Group BlendIf' group")
    elif gb[4] < 16 or bytes.fromhex(gb[5])[4:8] != bytes((128, 128, 255, 255)):
        bad.append("Blend If of a group not written: %d bytes %s" % (gb[4], gb[5]))
    for l in layers:
        if l[0] == "</Layer group>" and l[4] >= 8 and bytes.fromhex(l[5])[4:8] not in (
                b"", bytes((0, 0, 255, 255))):
            bad.append("Blend If written on a group's section divider: %s" % l[5])
    for name, key in EXPECT_ADJ.items():
        l = by.get(name)
        if l is None:
            bad.append("no layer %s" % name)
        elif key not in [k for k, _ in l[6]]:
            bad.append("%s: no '%s' block (keys %s)" % (name, key, [k for k, _ in l[6]]))
    return bad


if __name__ == "__main__":
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    status = 0
    for p in paths:
        nch, ls = dump(p)
        print("==", p, "document channels", nch)
        for name, mode, clip, chans, br, brd, keys in ls:
            print("  %-22s %s clip=%d ch=%s blend-if=%d %s keys=%s" % (
                name[:22], mode, clip, chans, br, brd[:32], [k for k, _ in keys]))
        if "--check-smoke" in sys.argv:
            bad = check_smoke(ls)
            for b in bad:
                print("FAIL:", b)
            if bad:
                status = 1
            else:
                print("PSD smoke check passed")
    sys.exit(status)
