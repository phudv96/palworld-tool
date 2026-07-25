#!/usr/bin/env python3
# List every character (UID -> name) in a Palworld Level.sav, supports Oodle saves (PlM).
# Usage:
#   python D:\Tool\find_char_uid.py <world_dir_or_Level.sav> [character_name]
# Examples:
#   python D:\Tool\find_char_uid.py "D:\Tool\SaveGames\...\<world>"            # list all
#   python D:\Tool\find_char_uid.py "D:\Tool\SaveGames\...\<world>" Puddy      # search only for 'Puddy'
import sys, os, struct, io, contextlib

# Load the Oodle-capable package in palworld-host-save-fix-main (runnable from any cwd)
sys.path.insert(0, r"d:\Tool\palworld-host-save-fix-main")
from palworld_save_tools.gvas import GvasFile
from palworld_save_tools.palsav import decompress_sav_to_gvas
from palworld_save_tools.paltypes import PALWORLD_CUSTOM_PROPERTIES, PALWORLD_TYPE_HINTS

_DIS = ("MapObject","Foliage","CharacterSaveParameterMap.Value.RawData","ItemContainerSaveData",
        "CharacterContainerSaveData","DynamicItemSaveData","BaseCampSaveData","WorkSaveData","GroupSaveDataMap")
CUSTOM = {k:v for k,v in PALWORLD_CUSTOM_PROPERTIES.items() if not any(d in k for d in _DIS)}

def load_gvas(raw):
    with contextlib.redirect_stdout(io.StringIO()):
        return GvasFile.read(raw, PALWORLD_TYPE_HINTS, CUSTOM, allow_nan=True).dump()

def read_fstr(b,off):
    (sz,)=struct.unpack_from("<i",b,off);off+=4
    if sz==0: return "",off
    if sz<0: n=-sz; return b[off:off+n*2-2].decode("utf-16-le","replace"),off+n*2
    return b[off:off+sz-1].decode("utf-8","replace"),off+sz

def player_shaped(u):
    return u[4:12]==b"\x00"*8 and u[13:16]==b"\x00"*3

def find_players(blob):
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
        cands.append((c,cnt,pl))
    cands.sort(key=lambda x:(-x[1],x[0]))
    return cands[0] if cands else None

def uid32(u):
    # .sav filename form (first 4 bytes reversed + zeros)
    return "".join(f"{b:02x}" for b in u[:4][::-1]) + "0"*24

def main():
    if len(sys.argv) < 2:
        print("usage: python find_char_uid.py <world_dir_or_Level.sav> [character_name]"); sys.exit(1)
    p = sys.argv[1]
    level = os.path.join(p, "Level.sav") if os.path.isdir(p) else p
    if not os.path.exists(level):
        print(f"ERROR: Level.sav not found at: {level}"); sys.exit(1)
    target = sys.argv[2].lower() if len(sys.argv) > 2 else None

    raw,st = decompress_sav_to_gvas(open(level,"rb").read())
    print(f"Level.sav: {level}\n  decompressed {len(raw)} bytes (save_type {hex(st)})\n")
    lj = load_gvas(raw)
    groups = lj["properties"]["worldSaveData"]["value"]["GroupSaveDataMap"]["value"]

    print("=== Guilds (UID -> character name) ===")
    name_to_uid = {}
    for gi,g in enumerate(groups):
        if g["value"]["GroupType"]["value"]["value"] != "EPalGroupType::Guild": continue
        blob = bytes(g["value"]["RawData"]["value"]["values"])
        r = find_players(blob)
        if not r: continue
        c,cnt,pl = r; admin = blob[c-16:c]
        print(f"guild #{gi}: admin={admin[:4][::-1].hex().upper()}  members({cnt}):")
        for u,nm in pl:
            fn = uid32(u)
            print(f"    {fn.upper()}  (prefix {u[:4][::-1].hex().upper()})  = '{nm}'")
            name_to_uid.setdefault(nm.lower(), (nm, fn))
    print()
    if target:
        if target in name_to_uid:
            nm,fn = name_to_uid[target]
            print(f">>> MATCH: '{nm}' -> UID = {fn.upper()}")
        else:
            print(f">>> '{sys.argv[2]}' not found. Available names: {[v[0] for v in name_to_uid.values()]}")

if __name__ == "__main__":
    main()
