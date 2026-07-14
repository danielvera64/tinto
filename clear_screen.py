#!/usr/bin/env python3
"""Wipes the e-paper panel to white and puts it to deep sleep.

Must be run ON THE RASPBERRY PI with the HAT attached. Clears BOTH
controller RAM planes: on the tri-color 2.7" HAT (B), plane 0x26 is the
RED plane -- images written there by demos survive every black/white
clear and keep showing (in red) until this plane is zeroed. Then runs
black/white exercise cycles to flush residual ghosting.
"""

import glob
import platform
import sys


def fail(msg):
    print(f"\nERROR: {msg}")
    sys.exit(1)


def preflight():
    if platform.system() != "Linux":
        fail("This script drives the physical panel and must be run on "
             "the Raspberry Pi, not on this machine.")
    try:
        import spidev  # noqa: F401
        import gpiozero  # noqa: F401
    except ImportError as exc:
        fail(f"Missing dependency ({exc.name}). Install with:\n"
             "  sudo apt install python3-spidev python3-gpiozero")
    if not glob.glob("/dev/spidev*"):
        fail("No /dev/spidev* device found — SPI is disabled. Enable it:\n"
             "  sudo raspi-config  ->  Interface Options -> SPI -> Yes\n"
             "then reboot and run this again.")


def main():
    preflight()
    import time

    from reader.display import make_bulk_epd

    try:
        epd = make_bulk_epd()
        print("init... (if this hangs for more than ~30 s, another "
              "process may be using the panel — stop the Tinto app / "
              "systemd service — or the HAT is not seated properly)")
        epd.init()

        white = [0xFF] * epd.frame_bytes
        black = [0x00] * epd.frame_bytes
        zeros = [0x00] * epd.frame_bytes

        print("zeroing the 0x26 plane (red on tri-color panels) ...")
        epd.write_plane(0x26, zeros)

        # Exercise the pixels: full black, then full white, a few times.
        # This flushes ghosting that a white-only clear leaves behind.
        for i in range(3):
            print(f"cycle {i + 1}/3: black ...")
            epd.write_plane(0x24, black)
            epd.TurnOnDisplay()
            time.sleep(0.5)
            print(f"cycle {i + 1}/3: white ...")
            epd.write_plane(0x24, white)
            epd.TurnOnDisplay()
            time.sleep(0.5)

        print("sleeping panel")
        epd.sleep()
        from waveshare_epd import epd2in7_V2
        epd2in7_V2.epdconfig.module_exit(cleanup=True)
        print("done — the panel should now be blank")
    except Exception as exc:
        text = str(exc).lower()
        if "busy" in text or "in use" in text or "lgpio" in text:
            fail(f"{exc}\nThe GPIO/SPI pins look like they are held by "
                 "another process. Stop any running app or demo first:\n"
                 "  sudo systemctl stop tinto   # if you set up the service\n"
                 "  pkill -f main.py")
        raise


if __name__ == "__main__":
    main()
