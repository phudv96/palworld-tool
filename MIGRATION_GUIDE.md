# Migration Guide (Operational Reference)

A practical, command-oriented companion to the [README](README.md). The README explains *why* and
*how* it works; this doc is the *do-this-then-that* reference with copy-paste commands and checklists.

---

## 0. Prerequisites

- Run the Python tools with the vendored, Oodle-capable library. The scripts self-load it from
  `palworld-host-save-fix-main/` via `sys.path`, so they work from any working directory. If you
  moved the repo, update the `sys.path.insert(...)` line at the top of each script.
- `<world_dir>` must contain `Level.sav` and `Players/<uid>.sav` for the source UID.
- The host (listen-server) UID is always `00000000000000000000000000000001`.

---

## 1. Identify the character UID

If you don't already know the UID:

```bash
python find_char_uid.py "<world_dir>"          # list all characters (UID -> name)
python find_char_uid.py "<world_dir>" Puddy    # find one; prints its .sav-filename UID
```

Example output:

```
guild #5: admin=347D5DE9  members(1):
    347D5DE9000000000000000000000000  (prefix 347D5DE9)  = 'PuDDy'
>>> MATCH: 'PuDDy' -> UID = 347D5DE9000000000000000000000000
```

---

## 2. Run the migration

Two equivalent options.

### Option A — interactive (recommended)

```bash
python migrate_tool.py "<world_dir>"
```

Then follow the prompts: it lists members → pick direction → pick member (or type the target
dedicated UID) → leave the output folder blank for a **dry-run**, or enter a folder to write.

### Option B — non-interactive CLI

```bash
# 1) Dry-run first (no files written)
python fix_uid_migrate.py "<world_dir>" <old_uid32> <new_uid32>

# 2) If the reported slots look right, write the patched files
python fix_uid_migrate.py "<world_dir>" <old_uid32> <new_uid32> --write <out_dir>
```

| Direction | `<old_uid32>` | `<new_uid32>` |
|-----------|---------------|---------------|
| **Local → Dedicated** | `00000000000000000000000000000001` | dedicated UID |
| **Dedicated → Local** | dedicated UID | `00000000000000000000000000000001` |

A clean run reports 4 `Level.sav` slots + 2 player slots and `round-trip + reparse succeeded`.

### Worked examples

```bash
# Local -> Dedicated (host becomes 347D5DE9...)
python fix_uid_migrate.py "<world_dir>" \
  00000000000000000000000000000001 347D5DE9000000000000000000000000 --write PATCHED_DEDICATED

# Dedicated -> Local (PuDDy comes back to the host slot)
python fix_uid_migrate.py "<world_dir>" \
  347D5DE9000000000000000000000000 00000000000000000000000000000001 --write PATCHED_LOCAL
```

> `fix_host_binary_patch.py` does the same thing for the Local → Dedicated direction only (source UID
> hardcoded to the host): `python fix_host_binary_patch.py "<world_dir>" <dedicated_uid32>`.

---

## 3. Deploy the patched files

Into the target world folder:

1. Stop the server/game and **back up the current save**.
2. Copy in the patched `Level.sav`.
3. Copy in `Players/<new_uid>.sav`.
4. Delete the old `Players/<old_uid>.sav`.
5. **Deploying onto a dedicated server → also delete `WorldOption.sav`** (see §4).
6. Linux only: `chown -R 1000:1000 <world>` and `chmod -R 755 <world>`.
7. Start the server/game and log in with that account.

---

## 4. Post-migration fix: `WorldOption.sav` (co-op → dedicated)

**Symptom:** the REST API logs `Unauthorized (AdminPassword is empty)` (HTTP 401) every few seconds
and admin/REST features break — even though `PalWorldSettings.ini` and the `ADMIN_PASSWORD` env are
correct.

**Cause:** a world migrated from a listen server carries a baked `WorldOption.sav` whose
`OptionSettings` (with an empty admin password) override `PalWorldSettings.ini`.

**Fix:**

```bash
rm <world>/WorldOption.sav      # server regenerates it from the .ini on restart
```

Verify:

```bash
docker exec <container> curl -s -o /dev/null -w "HTTP=%{http_code}\n" \
  -u admin:<ADMIN_PASSWORD> http://127.0.0.1:8212/v1/api/info
# expect HTTP=200, not 401
```

---

## 5. Server config, provisioning, and backups

See the README:

- [Docker config](README.md#81-docker)
- [One-shot provisioning](README.md#82-one-shot-provisioning) — `provision_palworld.sh`
- [Telegram backups](README.md#84-telegram-backup)
- [Shell-quoting pitfalls](README.md#85-operational-note-shell-quoting)

---

## 6. Migration checklist (co-op → dedicated)

- [ ] `python find_char_uid.py "<world>"` — confirm the target character/UID
- [ ] Dry-run: `python fix_uid_migrate.py "<world>" 000...001 <dedicated_uid>`
- [ ] Verify the reported slots (4 Level + 2 player) and `round-trip + reparse succeeded`
- [ ] Re-run with `--write <out>`
- [ ] Stop container → back up
- [ ] Upload patched `Level.sav` + `Players/<dedicated_uid>.sav`; delete `000...001.sav`
- [ ] **Delete `WorldOption.sav`**
- [ ] `chown -R 1000:1000` + `chmod -R 755`
- [ ] Start → verify REST returns HTTP 200
