# Windfall

A [STROOP](https://github.com/SM64-TAS-ABC/STROOP)-style real-time memory inspector and
manipulator for **The Legend of Zelda: The Wind Waker** running in Dolphin. Primary target is the
Japanese GameCube release (`GZLJ01`); USA (`GZLE01`) is also recognized to a degree.

## What it does

**Interactive map** (central panel)
- Live top-down map of the Great Sea in world coordinates: sea-chart grid (A–G / 1–7),
  island name markers, and Link's position/facing (with his face as the marker).
- **Drag Link to teleport** — pins his X/Z to the cursor while you drag; optional
  "freeze on release" holds him at the drop spot.
- **Collision overlay** — reads the loaded collision geometry (DZB meshes registered with
  the game's `dBgS` manager) straight out of memory and draws island/terrain outlines.
  Refreshes on stage change and periodically as rooms stream in; rendered via a cached
  raster so it stays smooth.
- Live actor markers with hover labels; optional cull-volume bounds overlay;
  pan / wheel-zoom; auto-follow Link; toggles for grid / islands / actors / collision.

**Camera control** (Camera panel)
- Live readout of eye / look-at center / FoV / bank.
- Lock any of them to override the game's camera (the camera update is paused while locked).
- Smooth eased transitions ("Go") between the current view and target values.
- **Move Camera pad** — arrow buttons fly the camera relative to where it faces
  (forward/back, strafe left/right, raise/lower), with an adjustable step size;
  hold a button to repeat. Moving auto-engages the eye/center locks so the game
  can't pull the camera back.
- **Keyboard fly** — the arrow keys (forward/back/strafe) and PageUp/PageDown
  (up/down) move the camera from anywhere in the app, with smooth ease-in and
  glide-out, normalized diagonals, and speed tied to the same Step value. Keys are
  ignored while a spinbox, slider, list, or text field has focus. While locked on
  an actor the same keys steer the orbit instead: ←/→ yaw around it, ↑/↓ dolly
  in/out, PgUp/PgDn tilt.

**Actors** (Actors panel)
- Live actor table (name, process, position, distance to Link), filterable.
- Aim the camera at an actor, fly the eye to it, or teleport Link to it.
- **Lock on / eye follow** — smoothly track a moving actor. While locked, left-drag on the
  map orbits the camera (yaw/pitch, wrap-around yaw) and the mouse wheel adjusts orbit distance.
- Double-click an island or actor on the map to aim the camera; double-click Link to restore.

**Movie helpers** (Movie panel)
- HUD hide (USA version).
- **Camera timeline** — capture camera poses as keyframes, reorder/retime them,
  scrub or play back with smooth easing (loop optional), and save/load timelines as JSON.
- **Visibility** — hide Link or any actor via the engine's own NODRAW flags
  (the actor stays loaded and active, just unrendered).

## Setup

Requires Python 3.9+ and a running Dolphin with Wind Waker loaded.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .
```

Dependencies (installed by the above): `PySide6`, `dolphin-memory-engine`.

## Running

Start Dolphin, load Wind Waker, then:

```powershell
# GUI
.venv\Scripts\python -m windfall

# Terminal probe (quick address verification — move Link and watch the numbers)
.venv\Scripts\python -m windfall.tools.probe

# Discover the writable player position (only needed when porting to a new version)
.venv\Scripts\python -m windfall.tools.discover --test-write
```

## Architecture

```
src/windfall/
  memory/hook.py        # DolphinHook: the only code that touches emulated RAM (big-endian explicit)
  addresses/version.py  # per-region address tables + game-id detection
  game/player.py        # typed Link accessors (position, angle)
  game/camera.py        # dCamera_c accessors (eye/center/up/fovy/bank, pause/freeze)
  game/actors.py        # live actor-queue enumeration
  game/collision.py     # dBgS collision registry -> DZB triangle meshes (XZ projected)
  core/poller.py        # worker-thread polling loop: snapshots out, held/queued writes in
                        #   + 1 kHz position hammer and ~300 Hz camera tracker threads
  ui/                   # PySide6: main window, connection bar, map, dockable panels
  tools/                # headless verification / address-discovery CLIs
```

Design rule: nothing but `memory/hook.py` imports `dolphin_memory_engine`, so the backend and
address tables stay swappable.

## Address sourcing

Static addresses come from the [zeldaret/tww](https://github.com/zeldaret/tww) decompilation symbol maps
(`config/<GAMEID>/symbols.txt`), cross-referenced with the
[WW-Hacking-Docs RAM map](https://github.com/LagoLunatic/WW-Hacking-Docs), and verified live
against a running game (the probe/discover tools exist for exactly that). Struct layouts
(camera, actors, collision) follow the decomp headers.
