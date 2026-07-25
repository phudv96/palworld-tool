# Palworld Save UID Migration Toolkit

A toolkit for **migrating a Palworld character between a listen (co-op) server and a dedicated
server** without losing progress, items, Pals, or guild ownership — by rewriting the character's
`PlayerUId` directly inside the save files with a safe, length-preserving binary patch.

It also bundles the operational tooling for running a Palworld dedicated server on Docker:
one-shot provisioning, automated Telegram backups, and fixes for the common post-migration pitfalls.

---

## Table of Contents

1. [Why this exists](#1-why-this-exists)
2. [Background: how Palworld saves & servers work](#2-background-how-palworld-saves--servers-work)
3. [The core problem: PlayerUId mismatch](#3-the-core-problem-playeruid-mismatch)
4. [How the migration works](#4-how-the-migration-works)
5. [Tools](#5-tools)
6. [Usage](#6-usage)
7. [Deploying a migrated save](#7-deploying-a-migrated-save)
8. [Dedicated server operations](#8-dedicated-server-operations)
9. [Repository layout](#9-repository-layout)
10. [Requirements & setup](#10-requirements--setup)
11. [Safety, limitations & credits](#11-safety-limitations--credits)

---

## 1. Why this exists

Palworld identifies every player character by a **`PlayerUId`** — a 16-byte GUID. The catch:

- On a **listen server** (a world hosted directly from the game client, "co-op"), the host's
  character always has the *special* fixed UID `00000000000000000000000000000001`.
- On a **dedicated server**, that same person's character is keyed by a **different, real UID**
  deterministically derived from their SteamID64 (e.g. `347D5DE9...`).

So when you move a co-op world onto a dedicated server, the host's character no longer matches the
UID the dedicated server expects — the host effectively "loses" their character and has to start
over, while everyone else joins normally. This toolkit rewrites that one identity (in both
directions) so the character carries over intact.

---

## 2. Background: how Palworld saves & servers work

### 2.1 Save file format (`.sav`)

Each `.sav` file is a small header followed by a compressed **GVAS** blob (Unreal Engine's
save-game serialization):

```
┌────────────┬────────────┬────────┬───────────┬──────────────────────────┐
│ uncompLen  │ compLen     │ magic  │ save_type │ compressed payload ...   │
│ 4 bytes    │ 4 bytes     │ 3 bytes│ 1 byte    │                          │
└────────────┴────────────┴────────┴───────────┴──────────────────────────┘
```

| Magic  | Compression | Notes                                             |
|--------|-------------|---------------------------------------------------|
| `PlZ`  | zlib        | Original format. Readable by stock save tools.    |
| `PlM`  | **Oodle**   | Newer game versions. Needs `oo2core_9_win64.dll`. |

> Modern Palworld writes `PlM` (Oodle). The vendored `palworld_save_tools/palsav.py` in this repo is
> **patched** to decode both `PlZ` and `PlM`; the stock upstream library only understands `PlZ`.

The two files that matter for migration:

- **`Level.sav`** — the world. Contains:
  - `CharacterSaveParameterMap` — every character (players + Pals), keyed by `PlayerUId` + `InstanceId`.
  - `GroupSaveDataMap` — guilds (admin UID, member list, per-character handle IDs).
- **`Players/<UID>.sav`** — one file per player account, holding that player's `PlayerUId` and `InstanceId`.

### 2.2 How a `PlayerUId` is serialized

A `PlayerUId` renders in the save-file name as a 32-hex-character string, but on disk the 16 raw
bytes are **byte-swapped in the first 4 bytes**, with the remaining bytes zero:

```
filename:  347D5DE9000000000000000000000000
raw bytes: E9 5D 7D 34 00 00 00 00 00 00 00 00 00 00 00 00
           └── first 4 bytes reversed ──┘

host UID:  00000000000000000000000000000001
raw bytes: 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00
```

### 2.3 Listen server vs dedicated server

| | Listen / co-op | Dedicated |
|---|---|---|
| Host character UID | Always `000...001` | Real Steam-derived UID (fixed per account) |
| Other players | Real Steam UIDs | Real Steam UIDs |
| Player save file | `Players/000...001.sav` | `Players/<realUID>.sav` |

A given Steam account maps to a **stable** dedicated UID, so `Players/<realUID>.sav` is consistent
across sessions on the same server.

---

## 3. The core problem: PlayerUId mismatch

Migrating a world between the two server types means one identity has to change:

```
LOCAL  ──▶ DEDICATED     host  000...001   ──▶   real UID  (e.g. 347D5DE9...)
DEDICATED ──▶ LOCAL      real UID           ──▶   host  000...001
```

Everything else about the character — inventory, level, base, and **Pal ownership** — is keyed off
the character's `InstanceId`, not its `PlayerUId`, so it survives as long as we rewrite the UID
consistently everywhere it appears as an *identity* (and deliberately leave Pal ownership handles
pointing at the original character).

---

## 4. How the migration works

### 4.1 Why not just decode → edit JSON → re-encode?

The obvious approach — fully decode `Level.sav` to JSON, change the UID, re-encode — **corrupts the
save** with current game versions. The GVAS structure round-trips imperfectly through the older
serializer (extra bytes, `EOF not reached`), producing a `Level.sav` that crashes the server with
`Save data is corrupted` and locks *everyone* out. Disabling problem decoders avoids the read crash
but still corrupts on write.

### 4.2 The binary-patch approach (length-preserving)

Instead of re-serializing, we:

1. **Decompress** the save to raw GVAS bytes (zlib or Oodle).
2. Replace **only** the specific 16-byte GUIDs that represent the character's identity — every
   replacement is 16 bytes for 16 bytes, so **nothing shifts** and the GVAS structure stays byte-for-byte
   valid everywhere else.
3. **Recompress** and write back.

Because the edit is length-preserving, the structural serializer never runs on the parts we don't
touch, so it cannot corrupt them.

### 4.3 The six slots that get patched

This mirrors exactly what a "proper" host-save fix would change, but done in place:

| # | File | Slot | How it is located |
|---|------|------|-------------------|
| 1 | `Level.sav` | `char_key_PlayerUId` — the character map key | Anchored on the character's `InstanceId` |
| 2 | `Level.sav` | `guild_handle_guid` — the character's handle inside its guild | 16 bytes before the same `InstanceId` |
| 3 | `Level.sav` | `guild.admin_player_uid` | Parsed from `GroupSaveDataMap` |
| 4 | `Level.sav` | `guild.players[]` entry for this character | Parsed from the guild member list |
| 5 | `Players/<uid>.sav` | `PlayerUId` (primary) | After a `PlayerUId` property marker |
| 6 | `Players/<uid>.sav` | `PlayerUId` (secondary) | Second `PlayerUId` occurrence |

**Pal ownership handles are intentionally left pointing at the original UID.** They key off the
character instance, and leaving them untouched is what lets a reverse migration (back to
`000...001`) automatically re-unite the character with its Pals.

### 4.4 Parse & patch flow (detailed)

```
                        ┌────────────────────────────────────────────┐
 read Players/<old>.sav │ decompress → GVAS                          │
                        │ extract InstanceId (IID) and PlayerUId     │
                        │ assert PlayerUId == OLD                     │
                        │ scan for 'PlayerUId' markers → 2 raw slots │
                        └───────────────────┬────────────────────────┘
                                            │
                        ┌───────────────────▼────────────────────────┐
 read Level.sav         │ decompress → GVAS                          │
                        │                                            │
                        │ (A) find every occurrence of IID:          │
                        │     • if 'InstanceId' precedes it →        │
                        │         nearest OLD before it = char key   │
                        │     • if OLD sits 16 bytes before it →     │
                        │         that is the guild handle guid      │
                        │                                            │
                        │ (B) decode GroupSaveDataMap guilds:        │
                        │     • admin_player_uid == OLD → slot       │
                        │     • each member entry == OLD → slot      │
                        │       (new format has +1 trailing byte     │
                        │        after each member name)             │
                        └───────────────────┬────────────────────────┘
                                            │
                        ┌───────────────────▼────────────────────────┐
 verify & apply         │ dedupe slots                               │
                        │ assert every slot currently == OLD  ◀── refuses to run if not
                        │ overwrite each slot with NEW (16→16 bytes) │
                        └───────────────────┬────────────────────────┘
                                            │
                        ┌───────────────────▼────────────────────────┐
 integrity check        │ recompress → decompress → must match       │
                        │ re-parse GVAS (must not raise)             │
                        │ then (optionally) write output files       │
                        └────────────────────────────────────────────┘
```

The **guild member parser** (`find_players`) is the subtle part. In current game versions each
member record is: `16-byte UID` + `8 bytes` + an FString name + **1 extra trailing byte**. The
parser brute-forces the member-count anchor, validates that every UID is "player-shaped"
(bytes 4–11 zero and bytes 13–15 zero), and reads names as UTF-8/UTF-16 FStrings, which makes it
robust to absolute file offsets changing between saves.

---

## 5. Tools

| Script | Purpose |
|--------|---------|
| **`migrate_tool.py`** | Interactive all-in-one: point it at a world, it lists members, you pick a direction and a member, it migrates. **Start here.** |
| `fix_uid_migrate.py` | Non-interactive, bidirectional CLI migration (`<old_uid> <new_uid>`). Good for scripting. |
| `fix_host_binary_patch.py` | One-way (`host 000...001 → dedicated`) with the source UID hardcoded. |
| `find_char_uid.py` | Maps character name → UID by reading `Level.sav`. Handy when you don't know a UID. |
| `provision_palworld.sh` | One-shot dedicated-server provisioning (Docker + optional restore + Telegram backup cron). |
| `palsav_oodle_backup.py` | A safety copy of the Oodle-capable `palsav.py` (see note below). |

> **Note on the vendored library.** The scripts import `palworld_save_tools` from
> `palworld-host-save-fix-main/`, whose `palsav.py` is patched to support Oodle (`PlM`) via
> `oo2core_9_win64.dll`. If that file is ever reverted to the stock zlib-only version, Oodle saves
> fail to load with `PlM instead of PlZ` — restore it from `palsav_oodle_backup.py`.

---

## 6. Usage

> All Python tools require Python 3 and are meant to run on the machine where the repo lives. The
> `palworld_save_tools` import path is currently hardcoded to `d:\Tool\palworld-host-save-fix-main`;
> adjust `sys.path.insert(...)` at the top of the scripts if your checkout is elsewhere.

### 6.1 Interactive (recommended)

```bash
python migrate_tool.py                       # will prompt for the world path
python migrate_tool.py "<world_dir>"         # or pass it directly
```

Flow: it prints all guild members → you choose **1) LOCAL → DEDICATED** or **2) DEDICATED → LOCAL**
→ you pick the member (or enter the target dedicated UID) → it previews the slots and, unless you
provide an output folder, runs a **dry-run** by default.

### 6.2 Find a character's UID

```bash
python find_char_uid.py "<world_dir>"          # list every character
python find_char_uid.py "<world_dir>" Puddy    # find one, print its UID
```

### 6.3 Non-interactive migration

```bash
# Dry-run (checks and reports only; no files written)
python fix_uid_migrate.py "<world_dir>" <old_uid32> <new_uid32>

# Write the patched files to an output folder
python fix_uid_migrate.py "<world_dir>" <old_uid32> <new_uid32> --write <out_dir>
```

| Direction | `<old_uid32>` | `<new_uid32>` |
|-----------|---------------|---------------|
| Local → Dedicated | `00000000000000000000000000000001` | the dedicated UID |
| Dedicated → Local | the dedicated UID | `00000000000000000000000000000001` |

`<world_dir>` must contain `Level.sav` and `Players/<old_uid>.sav`. **Always dry-run first**, verify
the reported slots, then re-run with `--write`.

---

## 7. Deploying a migrated save

1. Stop the target (server container or game).
2. Back up the current save.
3. Copy the patched `Level.sav` and `Players/<new_uid>.sav` into the target world.
4. Delete the old `Players/<old_uid>.sav`.
5. **If deploying onto a dedicated server, delete `WorldOption.sav`** (see §8.3).
6. Fix ownership/permissions on Linux (`chown -R 1000:1000`, `chmod -R 755`).
7. Start it back up.

---

## 8. Dedicated server operations

### 8.1 Docker

Image: `thijsvanloef/palworld-server-docker:latest`.

```bash
docker run -d --name palworld --restart unless-stopped \
  -p 8211:8211/udp \
  -v /home/ubuntu/palworld-data:/palworld \
  -e PUID=1000 -e PGID=1000 -e TZ=UTC \
  -e PLAYERS=32 -e SERVER_NAME=my-palworld \
  -e SERVER_PASSWORD=<CHANGE_ME> -e ADMIN_PASSWORD=<CHANGE_ME> \
  -e MULTITHREADING=true -e COMMUNITY=false \
  -e RCON_ENABLED=false \
  -e REST_API_ENABLED=true -e REST_API_PORT=8212 \
  -e UPDATE_ON_BOOT=true \
  thijsvanloef/palworld-server-docker:latest
```

### 8.2 One-shot provisioning

`provision_palworld.sh` installs Docker + dependencies, optionally restores a backup zip, and starts
the server with the config above. Secrets are read from the environment:

```bash
SERVER_PASSWORD=xxx ADMIN_PASSWORD=xxx \
BOT_TOKEN=xxx CHAT_ID=xxx SETUP_BACKUP_CRON=true \
./provision_palworld.sh
```

### 8.3 Post-migration gotcha: `WorldOption.sav`

A world migrated from a listen server drags along its baked `WorldOption.sav`, whose settings
**override `PalWorldSettings.ini`** on a dedicated server. Because the listen server had no admin
password, the REST API then spams `Unauthorized (AdminPassword is empty)` (HTTP 401) — even though
your `.ini` and `ADMIN_PASSWORD` env are correct.

**Fix:** delete `WorldOption.sav` from the world folder and restart; the server regenerates it from
the `.ini`. Progress is untouched.

### 8.4 Telegram backup

The provisioner can install a daily cron job that zips `Pal/Saved`, sends it to a Telegram chat via
the Bot API (50 MB `sendDocument` limit, guarded at 49 MB), and only deletes the local zip on a
confirmed `"ok":true` response.

### 8.5 Operational note: shell quoting

When pushing scripts to the server over `PowerShell → ssh → bash`, nested quotes, parentheses, and
`{{ }}` templates get mangled across the shell layers. The reliable pattern is: write the script to a
local file, `scp` it, strip CRLF with `sed -i 's/\r$//'`, then run it (and install cron via
`crontab <file>` rather than piping a string).

---

## 9. Repository layout

```
.
├── README.md                     # this file
├── MIGRATION_GUIDE.md            # detailed operational reference
├── migrate_tool.py               # interactive migration UI
├── fix_uid_migrate.py            # bidirectional CLI migration
├── fix_host_binary_patch.py      # one-way host→dedicated patch
├── find_char_uid.py              # character name → UID lookup
├── palsav_oodle_backup.py        # safety copy of the Oodle palsav.py
├── provision_palworld.sh         # dedicated-server provisioning
└── palworld-host-save-fix-main/  # vendored palworld_save_tools (Oodle-patched) + oo2core DLL
```

Not tracked (see `.gitignore`): `SaveGames/` (personal save data), migration output folders
(`PATCHED_*/`), and Python caches.

---

## 10. Requirements & setup

- **Windows** (the Oodle DLL is `oo2core_9_win64.dll`) with **Python 3**.
- No pip install needed — `palworld_save_tools` is vendored under `palworld-host-save-fix-main/` and
  loaded via `sys.path`. Just ensure that folder (with `oo2core_9_win64.dll`) sits next to the scripts,
  or update the hardcoded path.
- The dedicated-server tooling assumes a Linux host with Docker.

---

## 11. Safety, limitations & credits

- **Always back up** a save before deploying a patched copy. The tools default to dry-run and write
  to a separate output folder, but the deploy step is on you.
- The tools verify every target slot equals the expected old UID *before* patching and run a
  recompress/decompress/re-parse integrity check *after*, and refuse to write if anything is off.
- The target UID often already exists on the dedicated side as a fresh low-level character in a solo
  guild; after migration that stub is orphaned, which is harmless. Confirm you're migrating the right
  person by checking the character name (via `find_char_uid.py`) first.
- Save-format internals build on **[cheahjs/palworld-save-tools](https://github.com/cheahjs/palworld-save-tools)**.
  `oo2core_9_win64.dll` is Oodle, © RAD Game Tools / Epic Games, included only to decode saves locally.
- Provided as-is for personal use with your own saves. Not affiliated with Pocketpair.
```
