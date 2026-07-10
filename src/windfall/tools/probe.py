"""Terminal probe for quick address verification without launching the GUI.

    python -m windfall.tools.probe

Hooks Dolphin, prints the detected game id, then streams Link's position + facing angle once
per ~0.25s. Move Link in-game and confirm the numbers track. Ctrl+C to stop.
"""

from __future__ import annotations

import time

from ..addresses.version import detect_version
from ..game.player import Player
from ..memory.hook import DolphinHook


def main() -> int:
    hook = DolphinHook()
    print("Waiting for Dolphin (start emulation and load the game)…")
    while not hook.connect():
        time.sleep(0.5)

    game_id, version = detect_version(hook)
    print(f"Hooked. game_id={game_id!r} supported={version is not None}")
    if version is None:
        print("No address table for this game id — nothing to stream.")
        return 1

    print(f"Version: {version.label}")
    player = Player(hook, version.addr)
    print("Streaming Link position/angle. Move in-game to verify. Ctrl+C to stop.\n")
    try:
        while True:
            if not hook.is_connected():
                print("… lost hook, reconnecting")
                hook.connect()
                time.sleep(0.5)
                continue
            pos = player.get_position()
            ang = player.get_angle_y_degrees()
            if pos is None:
                print("pos: <unavailable>")
            else:
                print(f"X={pos.x:12.3f}  Y={pos.y:12.3f}  Z={pos.z:12.3f}  facing={ang:7.2f}°")
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
