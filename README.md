# Windfall

A [STROOP](https://github.com/SM64-TAS-ABC/STROOP)-style real-time memory inspector and
manipulator for **The Legend of Zelda: The Wind Waker** running in Dolphin. Supports both
the Japanese (`GZLJ01`) and USA (`GZLE01`) GameCube releases — player/camera control, actor
enumeration, and inventory editing all work on either.

## Features

**Interactive map** — top-down view of the Great Sea with the sea-chart grid, island
markers, live actors, and a collision overlay read straight out of memory.
- Drag Link to teleport, or right-click anywhere for a "Teleport Link Here" menu — it
  looks up the ground height under the click so he lands on solid ground instead of
  falling under the map.
- Double-click an actor (here or in the Actors panel) to lock on and eye-follow it;
  double-click Link to release and restore the camera.
- "By Terrain" toggle on the collision overlay: color each triangle by its actual
  in-game terrain type (grass/sand/stone/water/lava/...) instead of a flat fill.

**Camera control** — live eye/center/FoV/bank readout, lockable to override the game's own
camera. Smooth eased transitions between views, an arrow-button fly pad, and keyboard flying
(arrows + PageUp/PageDown) that steers an orbit instead while locked on an actor.

**Actors** — filterable live actor table with distance-to-Link. Lock on / eye-follow smoothly
tracks a moving actor; right-drag orbits the camera around it and the wheel adjusts distance.

**Movie helpers** — a camera timeline (keyframe, retime, scrub/play, save/load as JSON),
per-actor visibility (NODRAW, stays loaded), and HUD hide (USA only for now — JP
needs more decomp coverage of d_meter/d_map first, see `addresses/version.py`).

**Inventory** — icon-based editor using the game's real item/equipment icons. Stats, all
equipment options (including every Master Sword charge state and a magic meter that tracks
Double Magic), and the full 20-slot item grid (bottles included) all write immediately, no
"Apply" step.

## Setup

Requires Python 3.9+ and a running Dolphin with Wind Waker loaded.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

## Running

```powershell
# GUI
.venv\Scripts\python -m windfall

# Terminal probe (quick address verification — move Link and watch the numbers)
.venv\Scripts\python -m windfall.tools.probe

# Discover the writable player position (only needed when porting to a new version)
.venv\Scripts\python -m windfall.tools.discover --test-write
```

## Building a standalone .exe

```powershell
.venv\Scripts\python -m pip install -e .[build]
.venv\Scripts\python -m PyInstaller windfall.spec
```

Produces `dist\Windfall.exe` (~29 MB — the spec strips Qt modules/plugins the app never
uses, since PySide6's PyInstaller hook otherwise bundles most of Qt regardless of what's
actually imported).

## Architecture

```
src/windfall/
  memory/hook.py         # DolphinHook: the only code that touches emulated RAM (big-endian explicit)
  addresses/version.py   # per-region address tables + game-id detection
  game/player.py          # typed Link accessors (position, angle)
  game/camera.py          # dCamera_c accessors (eye/center/up/fovy/bank, pause/freeze)
  game/actors.py          # live actor-queue enumeration
  game/collision.py       # dBgS collision registry -> DZB triangle meshes + ground-height queries
  game/inventory.py       # player stats/equipment/inventory-slot read & write
  core/poller.py          # worker-thread polling loop: snapshots out, held/queued writes in
                          #   + 1 kHz position hammer and ~300 Hz camera tracker threads
  ui/                     # PySide6: main window, connection bar, map, dockable panels
  tools/                  # headless verification / address-discovery CLIs
```

Design rule: nothing but `memory/hook.py` imports `dolphin_memory_engine`, so the backend and
address tables stay swappable.

## Address sourcing

Static addresses for both regions come from the [zeldaret/tww](https://github.com/zeldaret/tww)
decompilation's per-version symbol maps (`config/<GAMEID>/symbols.txt`), cross-referenced with
the [WW-Hacking-Docs RAM map](https://github.com/LagoLunatic/WW-Hacking-Docs) and verified live
against a running game (`tools/probe.py` / `tools/discover.py` exist for exactly that). Struct
layouts (camera, actors, collision) follow the decomp headers. See the comments above `_JP`/`_USA`
in `addresses/version.py` for the handful of fields that needed extra care (mainly a documented
JP/USA layout difference affecting two pointer offsets).

## Credits

See [CREDITS.md](CREDITS.md):

- KlydeStorm on DeviantArt for the Wind Waker icons
- Zelda Reverse Engineering Team (zeldaret) on GitHub for the decompilation
- The Wind Waker Speedrun Community for everything
