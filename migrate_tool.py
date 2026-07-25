#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =====================================================================
#  Palworld UID Migration Tool (interactive)
#  - Enter world path -> show all members
#  - Choose migration direction (LOCAL<->DEDICATED)
#  - Choose member -> safe patch (length-preserving, supports Oodle/PlM)
#
#  Run:  python D:\Tool\migrate_tool.py  [world_dir]
# =====================================================================
import os, sys, struct, io, contextlib

# Load the Oodle-capable package (runnable from any cwd)
sys.path.insert(0, r"d:\Tool\palworld-host-save-fix-main")
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.palsav import decompress_sav_to_gvas, compress_gvas_to_sav
from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

HOST = "00000000000000000000000000000001"   # host UID on listen/local server

_DIS = ("MapObject","Foliage","CharacterSaveParameterMap.Value.RawData","ItemContainerSaveData",
        "CharacterContainerSaveData","DynamicItemSaveData","BaseCampSaveData","WorkSaveData","GroupSaveDataMap")
CUSTOM = {k:v for k,v in PALWORLD_CUSTOM_PROPERTIES.items() if not any(d in k for d in _DIS)}

# ---------- helpers ----------
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
    return u[4:12]==b"\x00"*8 and u[13:16]==b"\x00"*3

def uid32(u):
    return "".join(f"{b:02x}" for b in u[:4][::-1]) + "0"*24

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

# ---------- read members ----------
def list_members(level_path):
    raw,st = decompress_sav_to_gvas(open(level_path,"rb").read())
    lj = load_gvas(raw)
    groups = lj["properties"]["worldSaveData"]["value"]["GroupSaveDataMap"]["value"]
    members=[]   # (uid_raw, name, guild_idx, is_admin)
    for gi,g in enumerate(groups):
        if g["value"]["GroupType"]["value"]["value"] != "EPalGroupType::Guild": continue
        blob = bytes(g["value"]["RawData"]["value"]["values"])
        r = find_players(blob)
        if not r: continue
        c,cnt,pl = r; admin = blob[c-16:c]
        for u,nm in pl:
            members.append((u, nm, gi, u==admin))
    return members, len(raw), st

# ---------- migrate ----------
def do_migrate(world, old_uid, new_uid, out_dir=None):
    old_uid=old_uid.lower(); new_uid=new_uid.lower()
    OLD=str32_to_raw(old_uid); NEW=str32_to_raw(new_uid)
    src_p=os.path.join(world,"Players",old_uid.upper()+".sav")
    level =os.path.join(world,"Level.sav")
    if not os.path.exists(src_p):
        print(f"  ERROR: source player save not found: {src_p}"); return False

    # source player
    praw,pst=decompress_sav_to_gvas(open(src_p,"rb").read()); praw=bytearray(praw)
    pj=load_gvas(bytes(praw)); sd=pj["properties"]["SaveData"]["value"]
    IID=uraw(sd["IndividualId"]["value"]["InstanceId"]["value"])
    if uraw(sd["PlayerUId"]["value"])!=OLD:
        print("  ERROR: PlayerUId in source file does not match OLD uid"); return False

    p_slots=[]; s=0
    while True:
        o=praw.find(b"PlayerUId",s)
        if o<0: break
        s=o+1
        h=praw.find(OLD,o,o+0x60)
        if h>=0 and h not in p_slots: p_slots.append(h)

    # Level
    lraw,lst=decompress_sav_to_gvas(open(level,"rb").read()); lraw=bytearray(lraw)
    l_slots=[]
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

    lj=load_gvas(bytes(lraw))
    groups=lj["properties"]["worldSaveData"]["value"]["GroupSaveDataMap"]["value"]
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

    seen=set(); uniq=[]
    for tg,off in l_slots:
        if off in seen: continue
        seen.add(off); uniq.append((tg,off))

    print(f"\n  [Level.sav] slots to patch:")
    for tg,off in sorted(uniq,key=lambda x:x[1]):
        cur=lraw[off:off+16]
        print(f"     0x{off:06x}  {tg:<32} current={'OLD' if cur==OLD else cur.hex()}")
        if cur!=OLD:
            print(f"  ERROR: slot {tg} is not OLD uid -> abort"); return False
    print(f"  [player .sav] slots: {[hex(x) for x in p_slots]} (expect 2)")
    if bytes(lraw).count(NEW)>0 and new_uid!=HOST:
        print(f"  WARNING: target UID {new_uid[:8]} already exists in Level.sav -> old char/guild will be orphaned")

    # apply
    for h in p_slots: praw[h:h+16]=NEW
    for _,off in uniq: lraw[off:off+16]=NEW

    # integrity
    lsav=compress_gvas_to_sav(bytes(lraw),lst)
    l2,_=decompress_sav_to_gvas(lsav); assert bytes(l2)==bytes(lraw), "Level round-trip mismatch"
    psav=compress_gvas_to_sav(bytes(praw),pst)
    p2,_=decompress_sav_to_gvas(psav); assert bytes(p2)==bytes(praw), "Player round-trip mismatch"
    load_gvas(bytes(lraw)); load_gvas(bytes(praw))
    print(f"  Verify OK: Level {len(uniq)} slots, Player {len(p_slots)} slots; round-trip + reparse succeeded.")
    print(f"  Pal handles keep the old UID: {bytes(lraw).count(OLD)} remaining (intentional)")

    if out_dir:
        os.makedirs(os.path.join(out_dir,"Players"),exist_ok=True)
        with open(os.path.join(out_dir,"Level.sav"),"wb") as f: f.write(lsav)
        with open(os.path.join(out_dir,"Players",new_uid.upper()+".sav"),"wb") as f: f.write(psav)
        print(f"\n  >>> WROTE: {out_dir}\\Level.sav  and  Players\\{new_uid.upper()}.sav")
    else:
        print("\n  (DRY-RUN - no files written)")
    return True

# ---------- UI ----------
def ask(prompt, default=None):
    v=input(prompt).strip().lstrip("﻿").strip()
    return v if v else (default or "")

def norm_uid(s):
    s="".join(ch for ch in s.lower() if ch in "0123456789abcdef")
    return s if len(s)==32 else None

def main():
    print("="*60)
    print("   PALWORLD UID MIGRATION TOOL")
    print("="*60)
    world = sys.argv[1] if len(sys.argv)>1 else ask("Enter world path (folder containing Level.sav): ")
    world = world.strip('"').strip()
    level = os.path.join(world,"Level.sav")
    if not os.path.exists(level):
        print(f"ERROR: Level.sav not found at: {level}"); sys.exit(1)

    print("\nReading save...")
    members, dl, st = list_members(level)
    print(f"Level.sav OK (decompressed {dl} bytes, save_type {hex(st)})\n")
    if not members:
        print("No members found in any guild."); sys.exit(1)

    print("=== MEMBERS ===")
    for i,(u,nm,gi,adm) in enumerate(members,1):
        host_tag = "  <-- HOST(000..001)" if uid32(u)==HOST else ""
        print(f"  [{i}] {nm:<16} UID {uid32(u).upper()}  (guild #{gi}{', admin' if adm else ''}){host_tag}")
    host_present = any(uid32(u)==HOST for u,_,_,_ in members)

    print("\n=== MIGRATION DIRECTION ===")
    print("  [1] LOCAL -> DEDICATED   (host 000...001  ->  dedicated UID)")
    print("  [2] DEDICATED -> LOCAL   (dedicated UID   ->  host 000...001)")
    d = ask("Choose direction (1/2): ")

    if d=="1":
        if not host_present:
            print("WARNING: host member 000...001 not found in this save (continuing anyway if Players\\000...001.sav exists).")
        old_uid = HOST
        raw = ask("Enter target DEDICATED UID (32 hex chars): ")
        new_uid = norm_uid(raw)
        if not new_uid: print("Invalid UID (need 32 hex chars)."); sys.exit(1)
    elif d=="2":
        sel = ask("Enter the number of the member to migrate: ")
        try:
            idx=int(sel)-1; u=members[idx][0]
        except: print("Invalid choice."); sys.exit(1)
        old_uid = uid32(u)
        if old_uid==HOST:
            print("This member is already host 000...001."); sys.exit(1)
        new_uid = HOST
    else:
        print("Invalid direction."); sys.exit(1)

    print(f"\n  OLD (source):  {old_uid.upper()}")
    print(f"  NEW (target):  {new_uid.upper()}")
    out = ask("\nEnter output folder (Enter = DRY-RUN only, no writes): ")
    out = out.strip('"').strip() or None
    if out and not os.path.isabs(out):
        out = os.path.join("d:\\Tool", out)

    print("\n" + "-"*60)
    ok = do_migrate(world, old_uid, new_uid, out)
    print("-"*60)
    if ok and out:
        print(f"\nDONE. Copy Level.sav + Players\\{new_uid.upper()}.sav from '{out}' into the target world,")
        print(f"and delete the old Players\\{old_uid.upper()}.sav file.")
        if new_uid!=HOST:
            print("If deploying to DEDICATED: remember to DELETE WorldOption.sav in the target world (avoids REST AdminPassword error).")

if __name__=="__main__":
    main()
