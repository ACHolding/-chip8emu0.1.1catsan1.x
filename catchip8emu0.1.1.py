import tkinter as tk
from tkinter import filedialog, messagebox
import random, time, os, sys, io, wave, struct, math, shutil, subprocess, tempfile

# ---- Window: 600x400, centred on screen (mGBA-style compact window) ----
W, H = 600, 400
SW, SH, SCALE = 64, 32, 8            # 512x256 display inside the 600x400 window
ROM_START, FONT_START = 0x200, 0x50
CPU_HZ = 700
TITLE = "AC CHIP-8 Emulator 0.4"

FONT = bytes([
0xF0,0x90,0x90,0x90,0xF0,0x20,0x60,0x20,0x20,0x70,
0xF0,0x10,0xF0,0x80,0xF0,0xF0,0x10,0xF0,0x10,0xF0,
0x90,0x90,0xF0,0x10,0x10,0xF0,0x80,0xF0,0x10,0xF0,
0xF0,0x80,0xF0,0x90,0xF0,0xF0,0x10,0x20,0x40,0x40,
0xF0,0x90,0xF0,0x90,0xF0,0xF0,0x90,0xF0,0x10,0xF0,
0xF0,0x90,0xF0,0x90,0x90,0xE0,0x90,0xE0,0x90,0xE0,
0xF0,0x80,0x80,0x80,0xF0,0xE0,0x90,0x90,0x90,0xE0,
0xF0,0x80,0xF0,0x80,0xF0,0xF0,0x80,0xF0,0x80,0x80])

KEYMAP = {"1":1,"2":2,"3":3,"4":0xC,"q":4,"w":5,"e":6,"r":0xD,
          "a":7,"s":8,"d":9,"f":0xE,"z":0xA,"x":0,"c":0xB,"v":0xF}


class Chip8:
    def __init__(self):
        self.shift_vy = False
        self.inc_i = False
        self.jump_vx = False
        self.wrap = True
        self.reset()

    def reset(self):
        self.mem = bytearray(4096); self.mem[FONT_START:FONT_START+len(FONT)] = FONT
        self.v = [0]*16; self.i = 0; self.pc = ROM_START; self.stack = []
        self.dt = 0; self.st = 0; self.keys = [False]*16
        self.fb = [0]*(SW*SH); self.wait = None; self.dirty = True; self.lastop = 0

    def load(self, data):
        if len(data) > 4096-ROM_START: raise ValueError("ROM is too large for CHIP-8 RAM.")
        self.reset(); self.mem[ROM_START:ROM_START+len(data)] = data

    def bad(self, op, pc): raise RuntimeError(f"Unknown opcode 0x{op:04X} at 0x{pc:03X}")

    def cycle(self):
        if self.wait is not None: return
        if self.pc > 0xFFE: raise RuntimeError(f"PC outside memory: 0x{self.pc:03X}")
        pc0 = self.pc; op = (self.mem[self.pc] << 8) | self.mem[self.pc+1]
        self.lastop = op; self.pc += 2
        nnn = op & 0xFFF; nn = op & 0xFF; n = op & 0xF; x = (op >> 8) & 0xF; y = (op >> 4) & 0xF; top = op & 0xF000

        if op == 0x00E0:
            self.fb = [0]*(SW*SH); self.dirty = True
        elif op == 0x00EE:
            if not self.stack: raise RuntimeError("Stack underflow")
            self.pc = self.stack.pop()
        elif top == 0x0000: pass
        elif top == 0x1000: self.pc = nnn
        elif top == 0x2000:
            if len(self.stack) >= 16: raise RuntimeError("Stack overflow")
            self.stack.append(self.pc); self.pc = nnn
        elif top == 0x3000:
            if self.v[x] == nn: self.pc += 2
        elif top == 0x4000:
            if self.v[x] != nn: self.pc += 2
        elif top == 0x5000 and n == 0:
            if self.v[x] == self.v[y]: self.pc += 2
        elif top == 0x6000: self.v[x] = nn
        elif top == 0x7000: self.v[x] = (self.v[x]+nn) & 255
        elif top == 0x8000:
            if n == 0: self.v[x] = self.v[y]
            elif n == 1: self.v[x] |= self.v[y]
            elif n == 2: self.v[x] &= self.v[y]
            elif n == 3: self.v[x] ^= self.v[y]
            elif n == 4:
                a, b = self.v[x], self.v[y]; s = a+b; self.v[x] = s & 255; self.v[0xF] = int(s > 255)
            elif n == 5:
                a, b = self.v[x], self.v[y]; self.v[x] = (a-b) & 255; self.v[0xF] = int(a >= b)
            elif n == 6:
                a = self.v[y] if self.shift_vy else self.v[x]
                self.v[x] = (a >> 1) & 255; self.v[0xF] = a & 1
            elif n == 7:
                a, b = self.v[x], self.v[y]; self.v[x] = (b-a) & 255; self.v[0xF] = int(b >= a)
            elif n == 0xE:
                a = self.v[y] if self.shift_vy else self.v[x]
                self.v[x] = (a << 1) & 255; self.v[0xF] = (a >> 7) & 1
            else: self.bad(op, pc0)
        elif top == 0x9000 and n == 0:
            if self.v[x] != self.v[y]: self.pc += 2
        elif top == 0xA000: self.i = nnn
        elif top == 0xB000:
            self.pc = (nnn+(self.v[x] if self.jump_vx else self.v[0])) & 0xFFF
        elif top == 0xC000: self.v[x] = random.getrandbits(8) & nn
        elif top == 0xD000: self.draw(self.v[x], self.v[y], n)
        elif top == 0xE000:
            k = self.v[x] & 0xF
            if nn == 0x9E:
                if self.keys[k]: self.pc += 2
            elif nn == 0xA1:
                if not self.keys[k]: self.pc += 2
            else: self.bad(op, pc0)
        elif top == 0xF000:
            if nn == 0x07: self.v[x] = self.dt
            elif nn == 0x0A: self.wait = x
            elif nn == 0x15: self.dt = self.v[x]
            elif nn == 0x18: self.st = self.v[x]
            elif nn == 0x1E: self.i = (self.i+self.v[x]) & 0xFFF
            elif nn == 0x29: self.i = FONT_START+(self.v[x] & 0xF)*5
            elif nn == 0x33:
                if self.i+2 >= 4096: raise RuntimeError("BCD outside RAM")
                a = self.v[x]; self.mem[self.i] = a//100; self.mem[self.i+1] = (a//10) % 10; self.mem[self.i+2] = a % 10
            elif nn == 0x55:
                if self.i+x >= 4096: raise RuntimeError("FX55 outside RAM")
                for r in range(x+1): self.mem[self.i+r] = self.v[r]
                if self.inc_i: self.i = (self.i+x+1) & 0xFFF
            elif nn == 0x65:
                if self.i+x >= 4096: raise RuntimeError("FX65 outside RAM")
                for r in range(x+1): self.v[r] = self.mem[self.i+r]
                if self.inc_i: self.i = (self.i+x+1) & 0xFFF
            else: self.bad(op, pc0)
        else: self.bad(op, pc0)
        self.pc &= 0xFFF

    def draw(self, px, py, h):
        self.v[0xF] = 0
        px %= SW; py %= SH
        for row in range(h):
            if self.i+row >= 4096: break
            sprite = self.mem[self.i+row]
            for bit in range(8):
                if not sprite & (0x80 >> bit): continue
                xx, yy = px+bit, py+row
                if self.wrap: xx %= SW; yy %= SH
                elif xx >= SW or yy >= SH: continue
                p = yy*SW+xx
                if self.fb[p]: self.v[0xF] = 1
                self.fb[p] ^= 1
        self.dirty = True

    def keydown(self, k):
        self.keys[k] = True
        if self.wait is not None: self.v[self.wait] = k; self.wait = None
    def keyup(self, k): self.keys[k] = False
    def timers(self):
        if self.dt: self.dt -= 1
        if self.st: self.st -= 1


class AudioEngine:
    """
    Real CHIP-8 audio engine. The buzzer tone is synthesized in memory at
    startup (square wave with short fade to avoid clicks); no audio asset
    files ship with the program. Backends, best first:
      1. winsound   (Windows, in-memory looping playback)
      2. pygame     (any OS, if installed; looping Sound)
      3. afplay / paplay / aplay (macOS/Linux; tone written to a runtime temp file)
      4. Tk bell    (last-resort fallback)
    Features: enable/mute, volume, frequency, waveform (square/sine/triangle).
    """
    RATE = 22050

    def __init__(self, root):
        self.root = root
        self.enabled = True
        self.active = False
        self.frequency = 440
        self.volume = 0.5
        self.waveform = "square"
        self.backend = None
        self.wav_bytes = b""
        self.pg_sound = None
        self.proc = None
        self.tmp_path = None
        self.last_bell = 0.0
        self._detect_backend()
        self.rebuild()

    # ---- synthesis ----
    def _synth(self):
        # Use a whole number of cycles so the loop is seamless.
        cycles = max(1, int(self.frequency * 0.1))
        n = int(self.RATE * cycles / self.frequency)
        amp = int(32767 * max(0.0, min(1.0, self.volume)))
        buf = bytearray()
        for k in range(n):
            ph = (k * self.frequency / self.RATE) % 1.0
            if self.waveform == "sine": s = math.sin(2*math.pi*ph)
            elif self.waveform == "triangle": s = 4*abs(ph-0.5)-1
            else: s = 1.0 if ph < 0.5 else -1.0
            buf += struct.pack("<h", int(s*amp))
        return bytes(buf)

    def _wav(self, pcm):
        bio = io.BytesIO()
        with wave.open(bio, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.RATE)
            w.writeframes(pcm)
        return bio.getvalue()

    def _detect_backend(self):
        if sys.platform.startswith("win"):
            try:
                import winsound  # noqa: F401
                self.backend = "winsound"; return
            except Exception: pass
        try:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame
            pygame.mixer.pre_init(self.RATE, -16, 1, 512)
            pygame.mixer.init()
            self.pygame = pygame
            self.backend = "pygame"; return
        except Exception: pass
        for cmd in ("afplay", "paplay", "aplay"):
            if shutil.which(cmd):
                self.cmd = cmd; self.backend = "cmd"; return
        self.backend = "bell"

    def rebuild(self):
        was = self.active
        self.stop()
        pcm = self._synth()
        self.wav_bytes = self._wav(pcm)
        if self.backend == "pygame":
            try: self.pg_sound = self.pygame.mixer.Sound(buffer=pcm)
            except Exception: self.pg_sound = None; self.backend = "bell"
        if was: self.start()

    # ---- controls ----
    def set_enabled(self, on):
        self.enabled = bool(on)
        if not self.enabled: self.stop()

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v)); self.rebuild()

    def set_frequency(self, f):
        self.frequency = int(max(100, min(2000, f))); self.rebuild()

    def set_waveform(self, name):
        self.waveform = name; self.rebuild()

    def start(self):
        if self.active or not self.enabled: return
        self.active = True
        try:
            if self.backend == "winsound":
                import winsound
                winsound.PlaySound(self.wav_bytes, winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_LOOP)
            elif self.backend == "pygame" and self.pg_sound:
                self.pg_sound.play(loops=-1)
            elif self.backend == "cmd":
                self._start_cmd()
        except Exception:
            self.backend = "bell"

    def _start_cmd(self):
        # Loop short clips until stopped (afplay/paplay/aplay have no loop flag).
        if self.tmp_path is None:
            fd, self.tmp_path = tempfile.mkstemp(suffix=".wav"); os.close(fd)
        with open(self.tmp_path, "wb") as f: f.write(self.wav_bytes)
        self._spawn_cmd()

    def _spawn_cmd(self):
        try:
            self.proc = subprocess.Popen([self.cmd, self.tmp_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self.proc = None; self.backend = "bell"

    def stop(self):
        if not self.active and self.proc is None:
            return
        self.active = False
        try:
            if self.backend == "winsound":
                import winsound; winsound.PlaySound(None, winsound.SND_PURGE)
            elif self.backend == "pygame" and self.pg_sound:
                self.pg_sound.stop()
        except Exception: pass
        if self.proc is not None:
            try: self.proc.terminate()
            except Exception: pass
            self.proc = None

    def update(self):
        """Call every UI tick while the sound timer is running."""
        if not self.active or not self.enabled: return
        if self.backend == "cmd" and (self.proc is None or self.proc.poll() is not None):
            self._spawn_cmd()
        elif self.backend == "bell":
            now = time.perf_counter()
            if now - self.last_bell >= 0.08:
                self.last_bell = now
                try: self.root.bell()
                except Exception: pass

    def shutdown(self):
        self.stop()
        if self.tmp_path:
            try: os.remove(self.tmp_path)
            except Exception: pass
        if self.backend == "pygame":
            try: self.pygame.mixer.quit()
            except Exception: pass


class App:
    def __init__(self):
        self.root = tk.Tk(); self.root.title(TITLE)
        self.root.configure(bg="#0b1220"); self.root.resizable(False, False)
        self.center_window()

        self.cpu = Chip8(); self.rom = None; self.path = None; self.running = False
        self.audio = True; self.audio_engine = AudioEngine(self.root); self.last = time.perf_counter()
        self.cpu_acc = 0.0; self.timer_acc = 0.0
        self.make_menu(); self.make_ui()
        self.root.bind_all("<KeyPress>", self.keydown); self.root.bind_all("<KeyRelease>", self.keyup)
        self.root.bind_all("<Control-o>", lambda e: self.loadrom())
        self.root.bind_all("<Control-r>", lambda e: self.reset())
        self.root.bind_all("<space>", lambda e: self.pause())
        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)
        self.render(); self.loop(); self.root.mainloop()

    def center_window(self):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{W}x{H}+{max(0,(sw-W)//2)}+{max(0,(sh-H)//2)}")

    def make_menu(self):
        bar = tk.Menu(self.root)
        f = tk.Menu(bar, tearoff=0); f.add_command(label="Load ROM...", accelerator="Ctrl+O", command=self.loadrom)
        f.add_separator(); f.add_command(label="Exit", command=self.exit_app)
        g = tk.Menu(bar, tearoff=0); g.add_command(label="Play Game", command=self.play)
        g.add_command(label="Pause / Resume", accelerator="Space", command=self.pause)
        g.add_command(label="Reset", accelerator="Ctrl+R", command=self.reset)
        c = tk.Menu(bar, tearoff=0)
        self.qshift = tk.BooleanVar(); self.qinc = tk.BooleanVar(); self.qjump = tk.BooleanVar(); self.qwrap = tk.BooleanVar(value=True)
        c.add_checkbutton(label="Classic shifts use VY", variable=self.qshift, command=self.quirks)
        c.add_checkbutton(label="FX55/FX65 increment I", variable=self.qinc, command=self.quirks)
        c.add_checkbutton(label="BXNN jump quirk", variable=self.qjump, command=self.quirks)
        c.add_checkbutton(label="Wrap sprites", variable=self.qwrap, command=self.quirks)

        # Audio menu
        a = tk.Menu(bar, tearoff=0)
        self.aud = tk.BooleanVar(value=True)
        a.add_checkbutton(label="Audio On", variable=self.aud, command=self.toggle_audio)
        a.add_command(label="Test Beep", command=self.test_beep)
        a.add_separator()
        vol = tk.Menu(a, tearoff=0); self.vol_var = tk.IntVar(value=50)
        for p in (0, 25, 50, 75, 100):
            vol.add_radiobutton(label=f"{p}%", variable=self.vol_var, value=p, command=self.set_volume)
        a.add_cascade(label="Volume", menu=vol)
        pit = tk.Menu(a, tearoff=0); self.freq_var = tk.IntVar(value=440)
        for fr in (220, 330, 440, 660, 880):
            pit.add_radiobutton(label=f"{fr} Hz", variable=self.freq_var, value=fr, command=self.set_freq)
        a.add_cascade(label="Pitch", menu=pit)
        wf = tk.Menu(a, tearoff=0); self.wave_var = tk.StringVar(value="square")
        for w in ("square", "sine", "triangle"):
            wf.add_radiobutton(label=w.capitalize(), variable=self.wave_var, value=w, command=self.set_wave)
        a.add_cascade(label="Waveform", menu=wf)

        h = tk.Menu(bar, tearoff=0); h.add_command(label="Controls", command=self.help)
        h.add_command(label="About", command=self.about)
        bar.add_cascade(label="File", menu=f); bar.add_cascade(label="Game", menu=g)
        bar.add_cascade(label="Audio", menu=a)
        bar.add_cascade(label="Compatibility", menu=c); bar.add_cascade(label="Help", menu=h)
        self.root.config(menu=bar)

    def make_ui(self):
        self.stage = tk.Frame(self.root, bg="#0b1220")
        self.stage.pack(fill="both", expand=True)

        tk.Label(self.stage, text="AC CHIP-8", font=("TkDefaultFont", 14, "bold"),
                 bg="#0b1220", fg="white").place(relx=.5, y=10, anchor="n")

        # Display frame, dead centre of the 600x400 window.
        pw, ph = SW*SCALE+8, SH*SCALE+8
        self.viewport = tk.Frame(self.stage, bg="#121a2b", width=pw, height=ph)
        self.viewport.place(relx=.5, rely=.5, anchor="center")
        self.viewport.pack_propagate(False)

        self.canvas = tk.Canvas(self.viewport, width=SW*SCALE, height=SH*SCALE,
                                bg="black", highlightthickness=0, borderwidth=0)
        self.canvas.place(relx=.5, rely=.5, anchor="center")

        self.rect = []
        for y in range(SH):
            for x in range(SW):
                self.rect.append(self.canvas.create_rectangle(
                    x*SCALE, y*SCALE, (x+1)*SCALE, (y+1)*SCALE, outline="", fill="black"))
        self.shadow = [0]*(SW*SH)

        self.status = tk.StringVar(value="READY • File → Load ROM")
        tk.Label(self.stage, textvariable=self.status, bg="#0b1220", fg="white",
                 font=("TkDefaultFont", 10)).place(relx=.5, rely=1, y=-10, anchor="s")

    # ---- audio handlers ----
    def toggle_audio(self):
        self.audio = self.aud.get()
        self.audio_engine.set_enabled(self.audio)

    def set_volume(self): self.audio_engine.set_volume(self.vol_var.get()/100.0)
    def set_freq(self): self.audio_engine.set_frequency(self.freq_var.get())
    def set_wave(self): self.audio_engine.set_waveform(self.wave_var.get())

    def test_beep(self):
        if not self.audio: return
        self.audio_engine.start()
        self.root.after(250, self.audio_engine.stop)

    def about(self):
        messagebox.showinfo("About", f"{TITLE}\nReal CHIP-8 interpreter\nPython + Tkinter\n"
                            f"Audio engine: synthesized, backend = {self.audio_engine.backend}\nNo audio asset files.",
                            parent=self.root)

    def quirks(self):
        self.cpu.shift_vy = self.qshift.get(); self.cpu.inc_i = self.qinc.get()
        self.cpu.jump_vx = self.qjump.get(); self.cpu.wrap = self.qwrap.get()

    def exit_app(self):
        self.audio_engine.shutdown()
        self.root.destroy()

    def loadrom(self):
        p = filedialog.askopenfilename(parent=self.root, title="Load CHIP-8 ROM",
            filetypes=[("CHIP-8 ROM", "*.ch8 *.c8 *.rom"), ("All files", "*.*")])
        if not p: return
        try:
            with open(p, "rb") as fh: data = fh.read()
            self.cpu.load(data); self.quirks()
            self.rom = data; self.path = p; self.running = True; self.cpu_acc = 0.0; self.timer_acc = 0.0; self.last = time.perf_counter()
            self.root.title(TITLE+" — "+os.path.basename(p))
            self.status.set(f"PLAYING • {os.path.basename(p)} • {len(data)} bytes")
            self.render()
        except Exception as e: messagebox.showerror("ROM Error", str(e), parent=self.root)

    def play(self):
        if self.rom is None: self.loadrom()
        else:
            self.running = True; self.cpu_acc = 0.0; self.last = time.perf_counter()
            self.status.set("PLAYING • "+os.path.basename(self.path))

    def pause(self):
        if self.rom is None: return
        self.audio_engine.stop()
        self.running = not self.running
        self.last = time.perf_counter()
        self.status.set(("PLAYING • " if self.running else "PAUSED • ")+os.path.basename(self.path))

    def reset(self):
        if self.rom is None: return
        self.audio_engine.stop()
        self.cpu.load(self.rom); self.quirks(); self.running = True; self.cpu_acc = 0.0; self.timer_acc = 0.0; self.last = time.perf_counter(); self.render()
        self.status.set("RESET • PLAYING • "+os.path.basename(self.path))

    def help(self):
        messagebox.showinfo("CHIP-8 Controls",
        "CHIP-8     Keyboard\n1 2 3 C     1 2 3 4\n4 5 6 D     Q W E R\n7 8 9 E     A S D F\nA 0 B F     Z X C V\n\nSpace: pause/resume\nCtrl+O: load ROM\nCtrl+R: reset", parent=self.root)

    def keydown(self, e):
        k = e.keysym.lower()
        if k in KEYMAP: self.cpu.keydown(KEYMAP[k])
    def keyup(self, e):
        k = e.keysym.lower()
        if k in KEYMAP: self.cpu.keyup(KEYMAP[k])

    def render(self):
        fb, sh, rect, cv = self.cpu.fb, self.shadow, self.rect, self.canvas
        for i in range(SW*SH):
            p = fb[i]
            if p != sh[i]:
                sh[i] = p
                cv.itemconfig(rect[i], fill="white" if p else "black")
        self.cpu.dirty = False

    def loop(self):
        now = time.perf_counter()
        elapsed = min(now-self.last, 0.05)
        self.last = now

        if self.running:
            self.cpu_acc += elapsed * CPU_HZ
            self.timer_acc += elapsed * 60.0
            try:
                cycles = int(self.cpu_acc)
                if cycles:
                    self.cpu_acc -= cycles
                    for _ in range(min(cycles, 50)):
                        self.cpu.cycle()

                ticks = int(self.timer_acc)
                if ticks:
                    self.timer_acc -= ticks
                    for _ in range(min(ticks, 4)):
                        self.cpu.timers()

                if self.cpu.dirty:
                    self.render()

                # Sound timer > 0 -> buzzer on.
                if self.audio and self.cpu.st > 0:
                    self.audio_engine.start()
                    self.audio_engine.update()
                else:
                    self.audio_engine.stop()

            except Exception as e:
                self.running = False
                self.audio_engine.stop()
                self.status.set(f"STOPPED • {e}")
                messagebox.showerror("Emulation Error", str(e), parent=self.root)

        self.root.after(2, self.loop)


if __name__ == "__main__":
    App()
