r"""
General Palworld UID migrator (binary patch, length-preserving -> Level.sav stays intact).
Changes an OLD player UID to a NEW player UID in Level.sav + the player .sav.

Works BOTH directions:
  local/listen -> dedicated :  old=00000000000000000000000000000001  new=<steam dedicated uid>
  dedicated -> local/listen :  old=<steam dedicated uid>             new=00000000000000000000000000000001

Usage (run from inside D:\Tool\palworld-host-save-fix-main):
  python D:\Tool\fix_uid_migrate.py <world_dir> <old_uid32> <new_uid32> [--write <out_dir>]
    <world_dir> must contain Level.sav and Players/<old_uid>.sav
    without --write : DRY-RUN (reports slots + verification, writes nothing)
    with    --write : writes patched Level.sav and <new_uid>.sav into <out_dir>
"""
import os, sys, struct, io, contextlib
sys.path.insert(0, r"D:\Tool\palworld-host-save-fix-main")
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.palsav import decompress_sav_to_gvas, compress_gvas_to_sav
from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

_DIS = ("MapObject","Foliage","CharacterSaveParameterMap.Value.RawData","ItemContainerSaveData",
        "CharacterContainerSaveData","DynamicItemSaveData","BaseCampSaveData","WorkSaveData","GroupSaveDataMap")
CUSTOM = {k:v for k,v in PALWORLD_CUSTOM_PROPERTIES.items() if not any(d in k for d in _DIS)}

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

def player_shaped(u):
    # real player UID: only bytes 0-3 and/or byte 12 nonzero (covers host 000..01 and steam uids)
    return u[4:12]==b"\x00"*8 and u[13:16]==b"\x00"*3

def main():
    if len(sys.argv) < 4:
        print("usage: fix_uid_migrate.py <world_dir> <old_uid32> <new_uid32> [--write <out_dir>]"); sys.exit(1)
    world=sys.argv[1]; old_uid=sys.argv[2].lower(); new_uid=sys.argv[3].lower()
    write = "--write" in sys.argv
    out_dir = sys.argv[sys.argv.index("--write")+1] if write else None
    assert len(old_uid)==32 and len(new_uid)==32 and old_uid!=new_uid, "need two distinct 32-char UIDs"
    OLD=str32_to_raw(old_uid); NEW=str32_to_raw(new_uid)
    print(f"OLD {old_uid} ({OLD.hex()})  ->  NEW {new_uid} ({NEW.hex()})   write={write}\n")

    src_p=os.path.join(world,"Players",old_uid.upper()+".sav")
    level =os.path.join(world,"Level.sav")
    if not os.path.exists(src_p): print(f"ERROR: source player save not found: {src_p}"); sys.exit(1)

    # ---- source player InstanceId ----
    with open(src_p,"rb") as f: pdata=f.read()
    praw,pst=decompress_sav_to_gvas(pdata); praw=bytearray(praw)
    pj=load_gvas(bytes(praw))
    sd=pj["properties"]["SaveData"]["value"]
    IID=uraw(sd["IndividualId"]["value"]["InstanceId"]["value"])
    print(f"Source InstanceId = {IID.hex()}")
    assert uraw(sd["PlayerUId"]["value"])==OLD, "player save PlayerUId != OLD uid"

    # ---- player save: guid after each 'PlayerUId' property name ----
    p_slots=[]; s=0
    while True:
        o=praw.find(b"PlayerUId",s)
        if o<0: break
        s=o+1
        h=praw.find(OLD,o,o+0x60)
        if h>=0 and h not in p_slots: p_slots.append(h)
    print(f"[player .sav] PlayerUId slots to patch: {[hex(x) for x in p_slots]} (expect 2)")

    # ---- Level.sav ----
    with open(level,"rb") as f: ldata=f.read()
    lraw,lst=decompress_sav_to_gvas(ldata); lraw=bytearray(lraw)
    print(f"[Level.sav] save_type=0x{lst:02x} decompressed={len(lraw)}")

    l_slots=[]
    # (A) char-key PlayerUId + guild handle guid, anchored on InstanceId
    iid_occ=[]; s=0
    while True:
        o=lraw.find(IID,s)
        if o<0: break
        iid_occ.append(o); s=o+1
    for o in iid_occ:
        win_start=max(0,o-0x60); window=lraw[win_start:o]
        if b"InstanceId" in window:
            h=lraw.rfind(OLD,win_start,o)
            if h>=0: l_slots.append(("char_key_PlayerUId",h))
        if lraw[o-16:o]==OLD:
            l_slots.append(("guild_handle_guid",o-16))

    # (B) guild admin + OLD player-entry (players entry has +1 trailing byte in new format)
    lj=load_gvas(bytes(lraw))
    groups=lj["properties"]["worldSaveData"]["value"]["GroupSaveDataMap"]["value"]
    def find_players(blob, require=None):
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
            if not ok: continue
            if not all(player_shaped(u) for u,_ in pl): continue
            if require is not None and require not in {u for u,_ in pl}: continue
            cands.append((c,cnt,pl))
        cands.sort(key=lambda x:(-x[1],x[0]))
        return cands[0] if cands else None
    for gi,g in enumerate(groups):
        if g["value"]["GroupType"]["value"]["value"]!="EPalGroupType::Guild": continue
        blob=bytes(g["value"]["RawData"]["value"]["values"]); base=lraw.find(blob)
        if base<0: continue
        r=find_players(blob, require=OLD)
        if not r: continue
        c,cnt,pl=r; admin=blob[c-16:c]
        if admin==OLD: l_slots.append(("guild_admin",base+c-16))
        p=c+4
        for u,nm in pl:
            if u==OLD: l_slots.append((f"guild_player_entry('{nm}')",base+p))
            p+=24
            (ss,)=struct.unpack_from("<i",blob,p); p+= (4+(-ss)*2) if ss<0 else (4+ss)
            p+=1
        print(f"  guild #{gi}: admin={'OLD' if admin==OLD else 'other'} members={cnt} "+", ".join(nm for _,nm in pl))

    # overview of ALL guilds for safety
    print("\n[All guilds overview]")
    def tag(u):
        if u==OLD: return "OLD("+old_uid[:8]+")"
        if u==NEW: return "NEW("+new_uid[:8]+")"
        return u[:4].hex()
    for gi,g in enumerate(groups):
        if g["value"]["GroupType"]["value"]["value"]!="EPalGroupType::Guild": continue
        blob=bytes(g["value"]["RawData"]["value"]["values"])
        r=find_players(blob)
        if not r: continue
        c,cnt,pl=r; admin=blob[c-16:c]
        print(f"  guild #{gi}: admin={tag(admin)} members({cnt}): "+", ".join(f"{tag(u)}='{nm}'" for u,nm in pl))
    print(f"  NOTE: NEW uid {new_uid[:8]} already present in Level.sav: {bytes(lraw).count(NEW)>0} "
          f"(its existing char/guild will be orphaned after migration)")

    # dedupe + verify each slot is OLD
    seen=set(); uniq=[]
    for tg,off in l_slots:
        if off in seen: continue
        seen.add(off); uniq.append((tg,off))
    print("\n[Level.sav] slots to patch:")
    for tg,off in sorted(uniq,key=lambda x:x[1]):
        cur=lraw[off:off+16]
        print(f"   0x{off:06x}  {tg:<32} current={'OLD' if cur==OLD else cur.hex()}")
        assert cur==OLD, f"slot {tg}@0x{off:x} is not OLD uid!"

    # apply
    for h in p_slots: praw[h:h+16]=NEW
    for _,off in uniq: lraw[off:off+16]=NEW

    # verify integrity
    lsav=compress_gvas_to_sav(bytes(lraw),lst)
    l2,_=decompress_sav_to_gvas(lsav); assert bytes(l2)==bytes(lraw), "Level round-trip mismatch"
    psav=compress_gvas_to_sav(bytes(praw),pst)
    p2,_=decompress_sav_to_gvas(psav); assert bytes(p2)==bytes(praw), "Player round-trip mismatch"
    load_gvas(bytes(lraw)); load_gvas(bytes(praw))
    print(f"\nVerify OK: Level {len(uniq)} slots, Player {len(p_slots)} slots patched; round-trip + reparse succeeded.")
    print(f"Remaining OLD uid count in Level (incl. pal handles, left as-is): {bytes(lraw).count(OLD)}")

    if write:
        os.makedirs(os.path.join(out_dir,"Players"),exist_ok=True)
        with open(os.path.join(out_dir,"Level.sav"),"wb") as f: f.write(lsav)
        with open(os.path.join(out_dir,"Players",new_uid.upper()+".sav"),"wb") as f: f.write(psav)
        print(f"\nWROTE: {out_dir}\\Level.sav  and  Players\\{new_uid.upper()}.sav")
    else:
        print("\nDRY-RUN only (no files written).")

if __name__=="__main__":
    main()
