"""Desktop emulator: shows the e-paper frame in a Tk window.

Keys:
  Left / Up      previous page (KEY1)
  Right / Down / Space   next page (KEY2)
  Enter / m      menu (KEY3)
  Backspace / f  font size (KEY4)
  q / Escape     quit
"""

from .display import WIDTH, HEIGHT

SCALE = 2

KEYMAP = {
    "Left": "up", "Up": "up",
    "Right": "down", "Down": "down", "space": "down",
    "Return": "select", "m": "select",
    "BackSpace": "back", "f": "back",
    "h": "home",
    "bracketleft": "jump-back", "bracketright": "jump-forward",
    "g": "alt-up", "r": "alt-down",
}


class TkDisplay:
    def __init__(self, root, label, image_module, imagetk_module):
        self.width = WIDTH
        self.height = HEIGHT
        self._root = root
        self._label = label
        self._Image = image_module
        self._ImageTk = imagetk_module
        self._photo = None

    def show(self, image, full: bool = False, red_image=None):
        from .display import compose_red
        frame = compose_red(image, red_image)
        frame = frame.resize(  # keep the image's own orientation
            (frame.width * SCALE, frame.height * SCALE), resample=0)
        self._photo = self._ImageTk.PhotoImage(frame)
        self._label.configure(image=self._photo)
        self._root.title("Tinto" + (" (full refresh)" if full else ""))

    def sleep(self):
        pass

    def close(self):
        pass


def run(state, books_dir: str, start=None):
    import tkinter as tk
    from PIL import Image, ImageTk

    from .shell import Shell

    root = tk.Tk()
    root.title("Tinto")
    root.resizable(False, False)
    label = tk.Label(root, bd=0)
    label.pack()

    display = TkDisplay(root, label, Image, ImageTk)
    shell = Shell(display, state, books_dir, start=start,
                  on_quit=root.destroy)

    def on_key(event):
        keysym = event.keysym
        if keysym in ("q", "Escape"):
            root.destroy()
            return
        action = KEYMAP.get(keysym)
        if action:
            shell.handle(action)

    def on_tick():
        shell.tick()
        # fine-grained so debounced menu redraws flush on time
        root.after(int(shell.timeout() * 1000), on_tick)

    root.bind("<Key>", on_key)
    root.after(200, on_tick)
    root.mainloop()
