#!/usr/bin/env python3
"""GUI Tkinter para testear el dispensador por puerto serie.

Requisitos:
  pip install pyserial
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("ERROR: falta pyserial. Instala con: pip install pyserial")
    sys.exit(1)


DEFAULT_BAUDRATE = 115200
DEFAULT_COUNT = 10
DEFAULT_INTERVAL_S = 1.0
DEFAULT_RESULT_TIMEOUT_S = 20.0
LOG_DIR = Path(__file__).resolve().parent / "logs"

FIRMWARE_COMMANDS = [
    ("$D", "Dispensar", "Inicia ciclo de dispensado"),
    ("$RM", "Run Motor", "Enciende motor 1"),
    ("$SM", "Stop Motor", "Apaga motor 1"),
    ("$SVEL <0-255>", "Velocidad M1", "Configura PWM de motor 1"),
    ("$SVEL2 <0-255>", "Velocidad M2", "Configura PWM de motor 2"),
    ("$DM1 <ms>", "Delay M1", "Retardo de apagado motor 1"),
    ("$DM2 <ms>", "Delay M2", "Retardo de apagado motor 2"),
    ("$RP", "Run Pusher", "Enciende motor 2/pusher"),
    ("$SP", "Stop Pusher", "Apaga motor 2/pusher"),
    ("$LDR", "Leer LDR", "Lectura unica de LDR"),
    ("$LDRC", "LDR continuo ON", "Habilita stream continuo de LDR"),
    ("$LDRS", "LDR continuo OFF", "Detiene stream continuo de LDR"),
    ("$DEV <0|1>", "Modo dispositivo", "Selecciona modo normal/alterno"),
    ("$D2P <1-1000>", "Pulso trigger", "Ancho de pulso salida DEV (ms)"),
    ("$TOUT", "Leer timeout", "Muestra timeout actual de dispensado"),
    ("$TOUT <100-60000>", "Set timeout", "Configura timeout de dispensado (ms)"),
    ("$GLD", "Leer umbral GOLD", "Muestra umbral de deteccion GOLD"),
    ("$GLD <0-1023>", "Set umbral GOLD", "Configura umbral de deteccion GOLD"),
]


class SerialTester:
    def __init__(self, log_callback) -> None:
        self.ser: serial.Serial | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._rx_queue: deque[str] = deque(maxlen=200)
        self._rx_lock = threading.Lock()
        self.log_path: Path | None = None
        self._log_file = None
        self._log = log_callback

    def connect(self, port: str, baudrate: int) -> bool:
        if self.ser and self.ser.is_open:
            self._log("Ya hay una conexion abierta.")
            return True

        if not port:
            self._log("Primero configura un puerto.")
            return False

        try:
            self.ser = serial.Serial(port, baudrate, timeout=0.1)
        except Exception as exc:
            self._log(f"No se pudo abrir el puerto: {exc}")
            return False

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = LOG_DIR / f"serial_{stamp}.log"
        self._log_file = self.log_path.open("a", encoding="utf-8")

        self._reader_stop.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        self._log(f"Conectado a {port} @ {baudrate}")
        self._log(f"Log: {self.log_path}")
        return True

    def disconnect(self) -> None:
        self._reader_stop.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)

        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass

        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass

        self.ser = None
        self._reader_thread = None
        self._log_file = None
        self._log("Desconectado.")

    def send_line(self, line: str) -> bool:
        if not self.ser or not self.ser.is_open:
            self._log("No hay conexion serie activa.")
            return False

        payload = (line.strip() + "\n").encode("ascii", errors="ignore")
        try:
            self.ser.write(payload)
            self._log_entry("TX", line.strip())
            return True
        except Exception as exc:
            self._log(f"Error enviando comando: {exc}")
            return False

    def is_connected(self) -> bool:
        return bool(self.ser and self.ser.is_open)

    def _reader_loop(self) -> None:
        while not self._reader_stop.is_set():
            if not self.ser or not self.ser.is_open:
                time.sleep(0.05)
                continue
            try:
                raw = self.ser.readline()
            except Exception:
                time.sleep(0.05)
                continue
            if not raw:
                continue
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            with self._rx_lock:
                self._rx_queue.append(text)
            self._log_entry("RX", text)

    def _log_entry(self, direction: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{ts}] {direction}: {message}"
        self._log(line)
        if self._log_file:
            self._log_file.write(line + "\n")
            self._log_file.flush()


def list_serial_ports() -> list[str]:
    ports: list[str] = []
    for p in list_ports.comports():
        device = (p.device or "").lower()
        descriptor = " ".join(
            [
                p.description or "",
                p.manufacturer or "",
                p.hwid or "",
                p.name or "",
            ]
        ).lower()

        # Keep Windows COM ports and USB-like serial adapters.
        if device.startswith("com") or "usb" in descriptor or "usb" in device:
            ports.append(p.device)
    return ports


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Dispenser Test")
        self.resizable(True, True)
        self.tester = SerialTester(log_callback=self._append_log)
        self._test_running = False
        self._test_stop = threading.Event()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        # Main split: left controls/log and right firmware command list (full height)
        root_split = ttk.Frame(self)
        root_split.pack(fill="both", expand=True)

        left_panel = ttk.Frame(root_split)
        left_panel.pack(side="left", fill="both", expand=True)

        right_panel = ttk.LabelFrame(root_split, text="Comandos firmware")
        right_panel.pack(side="right", fill="y", padx=(0, 6), pady=6)

        # ---- Connection bar ----
        conn_frame = ttk.LabelFrame(left_panel, text="Conexión")
        conn_frame.pack(fill="x", **pad)

        ttk.Label(conn_frame, text="Puerto:").grid(row=0, column=0, sticky="w", **pad)
        self._port_var = tk.StringVar()
        self._port_combo = ttk.Combobox(conn_frame, textvariable=self._port_var, width=18)
        self._port_combo.grid(row=0, column=1, sticky="w", **pad)

        ttk.Button(conn_frame, text="⟳", width=3, command=self._refresh_ports).grid(
            row=0, column=2, **pad
        )

        ttk.Label(conn_frame, text="Baudrate:").grid(row=0, column=3, sticky="w", **pad)
        self._baud_var = tk.StringVar(value=str(DEFAULT_BAUDRATE))
        ttk.Entry(conn_frame, textvariable=self._baud_var, width=10).grid(
            row=0, column=4, sticky="w", **pad
        )

        self._connect_btn = ttk.Button(
            conn_frame, text="Conectar", command=self._toggle_connect
        )
        self._connect_btn.grid(row=0, column=5, **pad)

        self._status_var = tk.StringVar(value="Desconectado")
        ttk.Label(conn_frame, textvariable=self._status_var, foreground="red").grid(
            row=0, column=6, **pad
        )

        self._refresh_ports()

        # ---- Command buttons ----
        cmd_frame = ttk.LabelFrame(left_panel, text="Comandos")
        cmd_frame.pack(fill="x", **pad)

        commands = [
            ("$D",  "Dispensar"),
            ("$RM", "Run Motor"),
            ("$SM", "Stop Motor"),
            ("$RP", "Run Pusher"),
            ("$SP", "Stop Pusher"),
        ]

        for col, (cmd, label) in enumerate(commands):
            btn = ttk.Button(
                cmd_frame,
                text=f"{cmd}\n{label}",
                width=14,
                command=lambda c=cmd: self._send_cmd(c),
            )
            btn.grid(row=0, column=col, **pad)

        # ---- Test $D ----
        burst_frame = ttk.LabelFrame(left_panel, text="Test $D")
        burst_frame.pack(fill="x", **pad)

        ttk.Label(burst_frame, text="Cantidad:").grid(row=0, column=0, sticky="w", **pad)
        self._count_var = tk.StringVar(value=str(DEFAULT_COUNT))
        ttk.Entry(burst_frame, textvariable=self._count_var, width=6).grid(
            row=0, column=1, sticky="w", **pad
        )

        ttk.Label(burst_frame, text="Intervalo (s):").grid(row=0, column=2, sticky="w", **pad)
        self._interval_var = tk.StringVar(value=str(DEFAULT_INTERVAL_S))
        ttk.Entry(burst_frame, textvariable=self._interval_var, width=6).grid(
            row=0, column=3, sticky="w", **pad
        )

        ttk.Label(burst_frame, text="Timeout (s):").grid(row=0, column=4, sticky="w", **pad)
        self._timeout_var = tk.StringVar(value=str(DEFAULT_RESULT_TIMEOUT_S))
        ttk.Entry(burst_frame, textvariable=self._timeout_var, width=6).grid(
            row=0, column=5, sticky="w", **pad
        )

        self._burst_btn = ttk.Button(
            burst_frame, text="Iniciar test", command=self._start_burst
        )
        self._burst_btn.grid(row=0, column=6, **pad)

        # Doble counter
        self._doble_count = 0
        self._doble_var = tk.StringVar(value="Doble: 0")
        ttk.Button(burst_frame, textvariable=self._doble_var, width=12,
                   command=self._increment_doble).grid(row=0, column=7, **pad)

        # ---- Manual command ----
        manual_frame = ttk.LabelFrame(left_panel, text="Comando manual")
        manual_frame.pack(fill="x", **pad)

        self._manual_var = tk.StringVar()
        manual_entry = ttk.Entry(manual_frame, textvariable=self._manual_var, width=30)
        manual_entry.grid(row=0, column=0, sticky="ew", **pad)
        manual_entry.bind("<Return>", lambda _e: self._send_manual())
        manual_frame.columnconfigure(0, weight=1)

        ttk.Button(manual_frame, text="Enviar", command=self._send_manual).grid(
            row=0, column=1, **pad
        )

        # ---- Log area ----
        log_frame = ttk.LabelFrame(left_panel, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)

        self._log_text = scrolledtext.ScrolledText(
            log_frame, state="disabled", height=20, font=("Courier", 9)
        )
        self._log_text.pack(fill="both", expand=True, padx=4, pady=4)

        ttk.Button(log_frame, text="Limpiar log", command=self._clear_log).pack(
            anchor="e", padx=4, pady=(0, 4)
        )

        self._cmd_tree = ttk.Treeview(
            right_panel,
            columns=("cmd", "name"),
            show="headings",
            height=28,
        )
        self._cmd_tree.heading("cmd", text="Comando")
        self._cmd_tree.heading("name", text="Nombre")
        self._cmd_tree.column("cmd", width=180, stretch=False, anchor="w")
        self._cmd_tree.column("name", width=300, stretch=False, anchor="w")

        cmd_scroll = ttk.Scrollbar(right_panel, orient="vertical", command=self._cmd_tree.yview)
        self._cmd_tree.configure(yscrollcommand=cmd_scroll.set)

        self._cmd_tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        cmd_scroll.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=4)
        right_panel.rowconfigure(0, weight=1)
        right_panel.columnconfigure(0, weight=1)

        for cmd, name, detail in FIRMWARE_COMMANDS:
            self._cmd_tree.insert("", "end", values=(cmd, f"{name} - {detail}"))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _refresh_ports(self) -> None:
        ports = list_serial_ports()
        self._port_combo["values"] = ports
        if ports and not self._port_var.get():
            self._port_var.set(ports[0])

    def _append_log(self, message: str) -> None:
        """Thread-safe log append."""
        self.after(0, self._do_append_log, message)

    def _do_append_log(self, message: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", message + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _send_cmd(self, cmd: str) -> None:
        if not self.tester.is_connected():
            messagebox.showwarning("Sin conexión", "Conecta al puerto serie primero.")
            return
        self.tester.send_line(cmd)

    def _send_manual(self) -> None:
        cmd = self._manual_var.get().strip()
        if not cmd:
            return
        if not self.tester.is_connected():
            messagebox.showwarning("Sin conexión", "Conecta al puerto serie primero.")
            return
        self.tester.send_line(cmd)
        self._manual_var.set("")

    def _toggle_connect(self) -> None:
        if self.tester.is_connected():
            self.tester.disconnect()
            self._connect_btn.configure(text="Conectar")
            self._status_var.set("Desconectado")
            self._status_label_color("red")
        else:
            port = self._port_var.get().strip()
            try:
                baud = int(self._baud_var.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Baudrate inválido.")
                return
            ok = self.tester.connect(port, baud)
            if ok:
                self._connect_btn.configure(text="Desconectar")
                self._status_var.set("Conectado")
                self._status_label_color("green")

    def _status_label_color(self, color: str) -> None:
        for child in self.winfo_children():
            if isinstance(child, ttk.LabelFrame) and child.cget("text") == "Conexión":
                for widget in child.winfo_children():
                    if isinstance(widget, ttk.Label) and widget.cget("textvariable") == str(
                        self._status_var
                    ):
                        widget.configure(foreground=color)
                        return

    def _increment_doble(self) -> None:
        self._doble_count += 1
        self._doble_var.set(f"Doble: {self._doble_count}")

    def _start_burst(self) -> None:
        # If test is running, stop it
        if self._test_running:
            self._test_stop.set()
            return

        if not self.tester.is_connected():
            messagebox.showwarning("Sin conexión", "Conecta al puerto serie primero.")
            return
        try:
            count = int(self._count_var.get())
            interval = float(self._interval_var.get())
            timeout = float(self._timeout_var.get())
        except ValueError:
            messagebox.showerror("Error", "Valores de test inválidos.")
            return

        # Reset doble counter
        self._doble_count = 0
        self._doble_var.set("Doble: 0")

        self._test_running = True
        self._test_stop.clear()
        self._burst_btn.configure(text="Stop test")
        threading.Thread(
            target=self._burst_worker,
            args=(count, interval, timeout),
            daemon=True,
        ).start()

    def _burst_worker(self, count: int, interval: float, timeout: float) -> None:
        self._append_log(f"Iniciando test: {count} x $D, intervalo {interval:.3f}s tras respuesta")
        for i in range(count):
            # Check if stop was requested
            if self._test_stop.is_set():
                self._append_log("Test detenido por el usuario.")
                break

            # Clear queue so we only watch for responses to THIS command
            with self.tester._rx_lock:
                self.tester._rx_queue.clear()

            if not self.tester.send_line("$D"):
                self._append_log("Se detuvo el test por error de envío.")
                break
            self._append_log(f"[{i + 1}/{count}] enviado $D")

            end = time.monotonic() + timeout
            result = None
            while time.monotonic() < end:
                if self._test_stop.is_set():
                    break
                with self.tester._rx_lock:
                    for line in self.tester._rx_queue:
                        if "ERR:" in line or "TIMEOUT" in line:
                            result = line
                            break
                if result:
                    break
                time.sleep(0.02)

            if self._test_stop.is_set():
                self._append_log("Test detenido por el usuario.")
                break

            if result is None:
                self._append_log("Timeout esperando respuesta. Test detenido.")
                break

            # If response is not ERR:0, ask user whether to continue
            if "ERR:0" not in result:
                self._append_log(f"Respuesta inesperada: {result}. Test pausado.")
                continuar = self._ask_continue(result)
                if not continuar:
                    self._append_log("Test detenido por el usuario.")
                    break
                self._append_log("Continuando test...")
            else:
                # After a successful ERR:0, watch briefly for GOLD without stopping the test.
                gold_deadline = time.monotonic() + 1.0
                gold_found = False
                while time.monotonic() < gold_deadline:
                    if self._test_stop.is_set():
                        break
                    with self.tester._rx_lock:
                        for line in self.tester._rx_queue:
                            if "GOLD" in line.upper():
                                gold_found = True
                                break
                    if gold_found:
                        self._append_log("GOLD detectado.")
                        self._notify_gold()
                        break
                    time.sleep(0.02)

            # Interval AFTER receiving the response
            if i < count - 1 and interval > 0:
                time.sleep(interval)

        self._append_log("Test finalizado.")
        self._test_running = False
        self.after(0, lambda: self._burst_btn.configure(text="Iniciar test"))

    def _notify_gold(self) -> None:
        """Play a short ding and show a popup on UI thread without pausing the test flow."""

        def _show() -> None:
            try:
                self.bell()
            except Exception:
                pass
            messagebox.showinfo("GOLD", "gold!!")

        self.after(0, _show)

    def _ask_continue(self, result: str) -> bool:
        """Show a yes/no popup from the worker thread (thread-safe via event)."""
        answer_event = threading.Event()
        answer_holder = [False]

        def _show():
            answer_holder[0] = messagebox.askyesno(
                "Respuesta inesperada",
                f"Se recibió: {result}\n\n¿Deseas continuar el test?",
            )
            answer_event.set()

        self.after(0, _show)
        answer_event.wait()
        return answer_holder[0]

    def _on_close(self) -> None:
        self.tester.disconnect()
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
