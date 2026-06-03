#!/usr/bin/env python3
"""CLI para testear el dispensador por puerto serie.

Requisitos:
  pip install pyserial
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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


@dataclass
class Settings:
    port: str = ""
    baudrate: int = DEFAULT_BAUDRATE
    count: int = DEFAULT_COUNT
    interval_s: float = DEFAULT_INTERVAL_S
    result_timeout_s: float = DEFAULT_RESULT_TIMEOUT_S


class SerialTester:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ser: serial.Serial | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._rx_queue: deque[str] = deque(maxlen=200)
        self._rx_lock = threading.Lock()
        self.log_path: Path | None = None
        self._log_file = None

    def connect(self) -> bool:
        if self.ser and self.ser.is_open:
            print("Ya hay una conexion abierta.")
            return True

        if not self.settings.port:
            print("Primero configura un puerto.")
            return False

        try:
            self.ser = serial.Serial(self.settings.port, self.settings.baudrate, timeout=0.1)
        except Exception as exc:
            print(f"No se pudo abrir el puerto: {exc}")
            return False

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = LOG_DIR / f"serial_{stamp}.log"
        self._log_file = self.log_path.open("a", encoding="utf-8")

        self._reader_stop.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        print(f"Conectado a {self.settings.port} @ {self.settings.baudrate}")
        print(f"Log: {self.log_path}")
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

    def send_line(self, line: str) -> bool:
        if not self.ser or not self.ser.is_open:
            print("No hay conexion serie activa.")
            return False

        payload = (line.strip() + "\n").encode("ascii", errors="ignore")
        try:
            self.ser.write(payload)
            self._log("TX", line.strip())
            return True
        except Exception as exc:
            print(f"Error enviando comando: {exc}")
            return False

    def burst_dispense(self) -> None:
        print(
            f"Iniciando test: {self.settings.count} comandos $D cada {self.settings.interval_s:.3f}s"
        )

        for i in range(self.settings.count):
            with self._rx_lock:
                self._rx_queue.clear()

            if not self.send_line("$D"):
                print("Se detuvo el test por error de envio.")
                return
            print(f"[{i + 1}/{self.settings.count}] enviado $D")

            result = self._wait_for_dispense_result(self.settings.result_timeout_s)
            if result is None:
                print("Se detuvo el test por timeout esperando respuesta final (ERR:* o TIMEOUT).")
                return

            if "ERR:1" in result or "ERR:2" in result:
                if not self._ask_continue_after_error(result):
                    print("Se detuvo el test por decision del usuario.")
                    return

            if i < self.settings.count - 1 and self.settings.interval_s > 0:
                time.sleep(self.settings.interval_s)
        print("Test finalizado.")

    def _ask_continue_after_error(self, result: str) -> bool:
        while True:
            answer = input(f"Se recibio {result}. Deseas continuar? [s/N]: ").strip().lower()
            if answer in {"s", "si", "sí", "y", "yes"}:
                return True
            if answer in {"", "n", "no"}:
                return False
            print("Responde con s o n.")

    def _wait_for_dispense_result(self, timeout_s: float) -> str | None:
        end_time = time.monotonic() + timeout_s
        while time.monotonic() < end_time:
            with self._rx_lock:
                for line in self._rx_queue:
                    if "ERR:" in line or "TIMEOUT" in line:
                        return line
            time.sleep(0.02)
        return None

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
            self._log("RX", text)

    def _log(self, direction: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"[{ts}] {direction}: {message}"
        print(line)
        if self._log_file:
            self._log_file.write(line + "\n")
            self._log_file.flush()


def list_serial_ports() -> list[str]:
    return [p.device for p in list_ports.comports()]


def ask_int(prompt: str, default: int, minimum: int = 1) -> int:
    raw = input(f"{prompt} [{default}]: ").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
        if value < minimum:
            raise ValueError
        return value
    except ValueError:
        print(f"Valor invalido, uso default {default}.")
        return default


def ask_float(prompt: str, default: float, minimum: float = 0.0) -> float:
    raw = input(f"{prompt} [{default}]: ").strip()
    if raw == "":
        return default
    try:
        value = float(raw)
        if value < minimum:
            raise ValueError
        return value
    except ValueError:
        print(f"Valor invalido, uso default {default}.")
        return default


def print_menu() -> None:
    print("\n=== TEST APP DISPENSER ===")
    print("1) Listar puertos")
    print("2) Configurar puerto/baud")
    print("3) Conectar")
    print("4) Desconectar")
    print("5) Configurar test ($D cantidad/intervalo)")
    print("6) Ejecutar test de $D")
    print("7) Mandar $D")
    print("8) Enviar comando manual")
    print("9) Salir")


def main() -> None:
    settings = Settings()
    tester = SerialTester(settings)

    try:
        while True:
            print_menu()
            choice = input("Seleccion: ").strip()

            if choice == "1":
                ports = list_serial_ports()
                if not ports:
                    print("No se encontraron puertos serie.")
                else:
                    print("Puertos disponibles:")
                    for p in ports:
                        print(f"- {p}")

            elif choice == "2":
                ports = list_serial_ports()
                if ports:
                    print("Sugeridos:")
                    for p in ports:
                        print(f"- {p}")
                settings.port = input(f"Puerto [{settings.port or '/dev/ttyUSB0'}]: ").strip() or (
                    settings.port or "/dev/ttyUSB0"
                )
                settings.baudrate = ask_int("Baudrate", settings.baudrate, minimum=1)
                print(f"Config: port={settings.port} baud={settings.baudrate}")

            elif choice == "3":
                tester.connect()

            elif choice == "4":
                tester.disconnect()
                print("Desconectado.")

            elif choice == "5":
                settings.count = ask_int("Cantidad de comandos $D", settings.count, minimum=1)
                settings.interval_s = ask_float(
                    "Intervalo entre comandos (seg)", settings.interval_s, minimum=0.0
                )
                settings.result_timeout_s = ask_float(
                    "Timeout para esperar respuesta final (seg)",
                    settings.result_timeout_s,
                    minimum=0.1,
                )
                print(
                    "Test config: "
                    f"count={settings.count} interval={settings.interval_s:.3f}s "
                    f"result_timeout={settings.result_timeout_s:.3f}s"
                )

            elif choice == "6":
                if not (tester.ser and tester.ser.is_open):
                    print("Debes conectar primero.")
                    continue
                tester.burst_dispense()

            elif choice == "7":
                if not (tester.ser and tester.ser.is_open):
                    print("Debes conectar primero.")
                    continue
                tester.send_line("$D")

            elif choice == "8":
                if not (tester.ser and tester.ser.is_open):
                    print("Debes conectar primero.")
                    continue
                cmd = input("Comando a enviar (ej: $D, $LDR, $LDRC, $LDRS): ").strip()
                if cmd:
                    tester.send_line(cmd)

            elif choice == "9":
                break

            else:
                print("Opcion invalida.")

    finally:
        tester.disconnect()


if __name__ == "__main__":
    main()
