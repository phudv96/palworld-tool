"""
Binary patcher: change host UID (000...001) -> new UID (1929C8E5) in Palworld save,
WITHOUT re-serialising GVAS (length-preserving), so Level.sav structure stays intact.

Usage:
  python patch.py <world_dir> <new_uid32> [--write <out_dir>]
    <world_dir> must contain Level.sav and Players/00000000000000000000000000000001.sav
    without --write  : DRY-RUN (reports slots + verification, writes nothing)
    with    --write  : writes patched Level.sav and <new_uid>.sav into <out_dir>
"""
import os, sys, struct, io, contextlib
sys.path.insert(0, r"D:\Tool\palworld-host-save-fix-main")
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.palsav import decompress_sav_to_gvas, compress_gvas_to_sav
from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

_DIS = ("MapObject","Foliage","CharacterSaveParameterMap.Value.RawData","ItemContainerSaveData",
        "CharacterContainerSaveData","DynamicItemSaveData","BaseCampSaveData","WorkSaveData","GroupSaveDataMap")
CUSTOM = {k:v for k,v in PALWORLD_CUSTOM_PROPERTIES.items() if not any(d in k for d in _DIS)}

HOST_UID = "00000000000000000000000000000001"

def str32_to_raw(s):
    u=s.lower()
    A=int(u[0:8],16);B=int(u[8:12],16);C=int(u[12:16],16);D=int(u[16:20],16);E1=int(u[20:24],16);E2=int(u[24:32],16)
    b=bytearray(16)
    b[3]=(A>>24)&255;b[2]=(A>>16)&255;b[1]=(A>>8)&255;b[0]=A&255
    b[7]=(B>>8)&255;b[6]=B&255;b[5]=(C>>8)&255;b[4]=C&255
    b[0xB]=(D>>8)&255;b[0xA]=D&255;b[9]=(E1>>8)&255;b[8]=E1&255
    b[0xC]=E2&255;b[0xD]=(E2>>8)&255;b[0xE]=(E2>>16)&255;b[0xF]=(E2>>24)&255
    return bytes(b)

def load_gvas(raw):
    with contextlib.redirect_stdout(io.StringIO()):
        return GvasFile.read(raw, PALWORLD_TYPE_HINTS, CUSTOM, allow_nan=True).dump()

def read_fstr(b,off):
    (sz,)=struct.unpack_from("<i",b,off);off+=4
    if sz==0: return "",off
    if sz<0: n=-sz; return b[off:off+n*2-2].decode("utf-16-le","replace"),off+n*2
    return b[off:off+sz-1].decode("utf-8","replace"),off+sz

def uraw(u):
    rb=getattr(u,"raw_bytes",None)
    return bytes(rb) if rb is not None else str32_to_raw(str(u).replace("-",""))

def main():
    world=sys.argv[1]; new_uid=sys.argv[2].lower()
    write = "--write" in sys.argv
    out_dir = sys.argv[sys.argv.index("--write")+1] if write else None
    HOST=str32_to_raw(HOST_UID); NEW=str32_to_raw(new_uid)
    assert HOST!=NEW and len(new_uid)==32
    print(f"HOST {HOST.hex()}  ->  NEW {NEW.hex()}   (write={write})\n")

    host_p=os.path.join(world,"Players",HOST_UID+".sav")
    level =os.path.join(world,"Level.sav")

    # ---- host InstanceId ----
    with open(host_p,"rb") as f: pdata=f.read()
    praw,pst=decompress_sav_to_gvas(pdata); praw=bytearray(praw)
    pj=load_gvas(bytes(praw))
    sd=pj["properties"]["SaveData"]["value"]
    IID=uraw(sd["IndividualId"]["value"]["InstanceId"]["value"])
    print(f"Host InstanceId = {IID.hex()}")
    assert uraw(sd["PlayerUId"]["value"])==HOST, "player save PlayerUId != host UID"

    # ---- patch plan for player save: guid after each 'PlayerUId' property name ----
    p_slots=[]
    s=0
    while True:
        o=praw.find(b"PlayerUId",s)
        if o<0: break
        s=o+1
        h=praw.find(HOST,o,o+0x60)
        if h>=0 and h not in p_slots: p_slots.append(h)
    print(f"[player .sav] PlayerUId slots to patch: {[hex(x) for x in p_slots]} (expect 2)")

    # ---- Level.sav ----
    with open(level,"rb") as f: ldata=f.read()
    lraw,lst=decompress_sav_to_gvas(ldata); lraw=bytearray(lraw)
    print(f"[Level.sav] save_type=0x{lst:02x} decompressed={len(lraw)}")

    l_slots=[]; notes=[]
    # (A) char-key PlayerUId: IID occurrence preceded by 'InstanceId' text and a host uid
    iid_occ=[]; s=0
    while True:
        o=lraw.find(IID,s)
        if o<0: break
        iid_occ.append(o); s=o+1
    for o in iid_occ:
        win_start=max(0,o-0x60)
        window=lraw[win_start:o]
        if b"InstanceId" in window:
            h=lraw.rfind(HOST,win_start,o)
            if h>=0:
                l_slots.append(("char_key_PlayerUId",h));
        # guild handle: host uid immediately before IID
        if lraw[o-16:o]==HOST:
            l_slots.append(("guild_handle_guid",o-16))

    # (B) guild admin + host player-entry (players entry has +1 trailing byte in new format)
    lj=load_gvas(bytes(lraw))
    groups=lj["properties"]["worldSaveData"]["value"]["GroupSaveDataMap"]["value"]
    def player_shaped(u):
        # real player UID: only bytes 0-3 and/or byte 12 nonzero (covers host 000..01 too)
        return u[4:12]==b"\x00"*8 and u[13:16]==b"\x00"*3
    def find_players(blob):
        cands=[]
        for c in range(16,len(blob)-28):
            (cnt,)=struct.unpack_from("<i",blob,c)
            if not (1<=cnt<=50): continue
            p=c+4; pl=[]; ok=True
            for _ in range(cnt):
                if p+29>len(blob): ok=False;break
                uid=blob[p:p+16]; p+=16+8
                (ss,)=struct.unpack_from("<i",blob,p)
                if ss<-200 or ss>200: ok=False;break
                try: nm,p=read_fstr(blob,p)
                except: ok=False;break
                if any(ord(ch)<9 for ch in nm): ok=False;break
                p+=1   # NEW format: 1 extra byte per player
                pl.append((uid,nm))
            if not ok: continue
            if not all(player_shaped(u) for u,_ in pl): continue
            uids={u for u,_ in pl}
            if HOST in uids:                       # host is a guild member here
                cands.append((c,cnt,pl,p))
        # prefer the candidate with the most players (real roster), then earliest
        if cands:
            cands.sort(key=lambda x:(-x[1], x[0]))
            return cands[0]
        return None
    for gi,g in enumerate(groups):
        if g["value"]["GroupType"]["value"]["value"]!="EPalGroupType::Guild": continue
        blob=bytes(g["value"]["RawData"]["value"]["values"])
        base=lraw.find(blob)
        if base<0: continue
        r=find_players(blob)
        if not r: continue
        c,cnt,pl,endp=r
        admin=blob[c-16:c]
        host_here = any(u==HOST for u,_ in pl)
        if not host_here: continue
        if admin==HOST:
            l_slots.append(("guild_admin",base+c-16))
        # host player entry uid
        p=c+4
        for u,nm in pl:
            if u==HOST:
                l_slots.append((f"guild_player_entry('{nm}')",base+p))
            p+=16+8
            (ss,)=struct.unpack_from("<i",blob,p); p+= (4+(-ss)*2) if ss<0 else (4+ss)
            p+=1
        print(f"  guild #{gi}: admin={'HOST' if admin==HOST else 'other'} members={cnt} "+
              ", ".join(f"{nm}" for _,nm in pl))

    # verbose: list ALL guilds + members (names) for safety review
    print("\n[All guilds overview]")
    def find_any(blob):
        cands=[]
        for c in range(16,len(blob)-28):
            (cnt,)=struct.unpack_from("<i",blob,c)
            if not (1<=cnt<=50): continue
            p=c+4; pl=[]; ok=True
            for _ in range(cnt):
                if p+29>len(blob): ok=False;break
                uid=blob[p:p+16]; p+=24
                (ss,)=struct.unpack_from("<i",blob,p)
                if ss<-200 or ss>200: ok=False;break
                try: nm,p=read_fstr(blob,p)
                except: ok=False;break
                if any(ord(ch)<9 for ch in nm): ok=False;break
                p+=1; pl.append((uid,nm))
            if ok and all(u[4:12]==b"\x00"*8 and u[13:16]==b"\x00"*3 for u,_ in pl):
                cands.append((c,cnt,pl))
        cands.sort(key=lambda x:(-x[1],x[0]))
        return cands[0] if cands else None
    for gi,g in enumerate(groups):
        if g["value"]["GroupType"]["value"]["value"]!="EPalGroupType::Guild": continue
        blob=bytes(g["value"]["RawData"]["value"]["values"])
        r=find_any(blob)
        if not r:
            continue
        c,cnt,pl=r
        admin=blob[c-16:c]
        def tag(u):
            if u==HOST: return "HOST(000..01)"
            if u==NEW:  return "TARGET("+new_uid[:8]+")"
            return u[:4].hex()
        print(f"  guild #{gi}: admin={tag(admin)}  members({cnt}): "+
              ", ".join(f"{tag(u)}='{nm}'" for u,nm in pl))
    print(f"  NOTE: target {new_uid[:8]} currently exists in Level.sav char-keys: "
          f"{bytes(lraw).count(NEW)>0} (will be orphaned after migration)")

    # dedupe
    seen=set(); uniq=[]
    for tag,off in l_slots:
        if off in seen: continue
        seen.add(off); uniq.append((tag,off))
    print("\n[Level.sav] slots to patch:")
    for tag,off in sorted(uniq,key=lambda x:x[1]):
        cur=lraw[off:off+16]
        print(f"   0x{off:06x}  {tag:<32} current={'HOST' if cur==HOST else cur.hex()}")
        assert cur==HOST, f"slot {tag}@0x{off:x} is not HOST uid!"

    # ---- apply ----
    for h in p_slots: praw[h:h+16]=NEW
    for _,off in uniq: lraw[off:off+16]=NEW

    # ---- verify integrity ----
    lsav=compress_gvas_to_sav(bytes(lraw),lst)
    l2,_=decompress_sav_to_gvas(lsav); assert bytes(l2)==bytes(lraw), "Level round-trip mismatch"
    psav=compress_gvas_to_sav(bytes(praw),pst)
    p2,_=decompress_sav_to_gvas(psav); assert bytes(p2)==bytes(praw), "Player round-trip mismatch"
    # confirm gvas still parses
    load_gvas(bytes(lraw)); load_gvas(bytes(praw))
    print(f"\nVerify OK: Level {len(l_slots)} slots, Player {len(p_slots)} slots patched; "
          f"round-trip + reparse succeeded.")
    # confirm no leftover HOST at patched semantic spots
    print(f"Remaining HOST uid count in Level (incl. pal handles, expected many): {bytes(lraw).count(HOST)}")

    if write:
        os.makedirs(os.path.join(out_dir,"Players"),exist_ok=True)
        with open(os.path.join(out_dir,"Level.sav"),"wb") as f: f.write(lsav)
        with open(os.path.join(out_dir,"Players",new_uid.upper()+".sav"),"wb") as f: f.write(psav)
        print(f"\nWROTE: {out_dir}\\Level.sav  and  Players\\{new_uid.upper()}.sav")
    else:
        print("\nDRY-RUN only (no files written).")

if __name__=="__main__":
    main()
