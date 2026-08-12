"""NativeEngine — ctypes-Brücke zur C++-Engine (libquelibrium.so).

Liefert den Hardware-State (Lorenz-Koordinaten, Entropie, Temperatur) und die
adaptiven Lorenz-Parameter, die ChaosRetrieval für Warp und Exploration nutzt.
Ohne .so läuft alles im Shadow Mode (statischer State) — Suche funktioniert
dann weiter, nur ohne Hardware-Modulation.

Bewusst NICHT portiert: `raw_search` (spectral_block_search) — dokumentierter
Vorbefund aus vibelike: query-unabhängig (dist=0); ChaosRetrieval ist der
tragfähige Such-Pfad. Ebenso `validate()` (4-Phasen), im Retrieval ungenutzt.
"""

from __future__ import annotations

import ctypes
import logging
import math
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Im Paket vendored (48 KB, identisch in collect + vibelike)
DEFAULT_LIB_FILE = Path(__file__).parent / "lib" / "libquelibrium.so"

SHADOW_STATE = {
    "x1": 0.0, "y1": 0.0, "z1": 0.0, "w1": 0.0,
    "entropy": 0.0, "temperature": 45.0,
    "x2": 0.0, "y2": 0.0, "cortex_bias": 0.5,
    "cpu_temp": -1.0, "gpu_temp": -1.0, "gpu_power": -1.0,
    "rdtsc_jitter": 0.0, "hw_timestamp": 0.0,
}

SHADOW_HARDWARE = {
    "cpu_temp": -1.0, "gpu_temp": -1.0, "gpu_power": -1.0,
    "rdtsc_jitter": 0.0, "hw_timestamp": 0.0,
}

SHADOW_LORENZ = {
    "rho": 28.0, "sigma": 10.0, "beta": 8.0 / 3.0,
    "reason": 0.5, "cycle": 0, "rho_delta": 0.0,
}


def _bind_signatures(lib) -> None:
    c_vp = ctypes.c_void_p
    c_int = ctypes.c_int
    c_f = ctypes.c_float
    c_d = ctypes.c_double
    c_cp = ctypes.c_char_p
    pf = ctypes.POINTER(ctypes.c_float)
    pd = ctypes.POINTER(ctypes.c_double)

    lib.init_quelibrium.argtypes = [c_int]
    lib.init_quelibrium.restype = c_vp
    lib.pulse_system.argtypes = [c_vp, c_f]
    lib.get_system_state.argtypes = [c_vp, pf]
    lib.apply_cortex_feedback.argtypes = [c_vp, c_int]
    lib.get_cortex_bias.argtypes = [c_vp, c_int]
    lib.get_cortex_bias.restype = c_f
    lib.set_lorenz_params.argtypes = [c_vp, c_d, c_d, c_d, c_d]
    lib.set_lorenz_params.restype = None
    lib.get_lorenz_params.argtypes = [c_vp, pd]
    lib.get_lorenz_params.restype = None
    lib.free_quelibrium.argtypes = [c_vp]

    lib.get_system_state_v2.argtypes = [c_vp, pf]
    lib.get_hardware_state.argtypes = [c_vp, pf]
    lib.enable_hardware_log.argtypes = [c_vp, c_cp]
    lib.disable_hardware_log.argtypes = [c_vp]
    lib.hardware_log_enabled.argtypes = [c_vp]
    lib.hardware_log_enabled.restype = c_int


class NativeEngine:
    """Eine Engine-Instanz pro Vault (eigener C++-Kontext, wie im Alt-System)."""

    def __init__(self, lib_file: Optional[Path] = None, buffer_mb: int = 64):
        self.lib = None
        self.ctx = None
        self.active = False
        path = Path(lib_file) if lib_file else DEFAULT_LIB_FILE

        if not path.exists():
            logger.warning("libquelibrium.so nicht gefunden (%s) — Shadow Mode", path)
            return
        try:
            lib = ctypes.CDLL(str(path))
            _bind_signatures(lib)
            ctx = lib.init_quelibrium(buffer_mb)
            if ctx:
                self.lib = lib
                self.ctx = ctx
                self.active = True
                logger.info("Quelibrium-Engine online (8D-Chaos | Cortex | Thermal)")
        except Exception as e:
            logger.error("Engine-Init fehlgeschlagen: %s — Shadow Mode", e)

    def get_hardware_state(self) -> dict:
        if not self.active:
            return SHADOW_STATE.copy()
        try:
            data = (ctypes.c_float * 14)()
            self.lib.get_system_state_v2(self.ctx, data)
        except Exception:
            data = (ctypes.c_float * 9)()
            self.lib.get_system_state(self.ctx, data)
        if any(math.isnan(data[i]) or math.isinf(data[i]) for i in range(len(data))):
            logger.error("Engine liefert NaN/Inf — Shadow Mode (Engine war divergiert)")
            self.active = False
            return SHADOW_STATE.copy()
        result = {
            "x1": float(data[0]), "y1": float(data[1]),
            "z1": float(data[2]), "w1": float(data[3]),
            "entropy": float(data[4]),
            "temperature": float(data[5]),
            "x2": float(data[6]), "y2": float(data[7]),
            "cortex_bias": float(data[8]),
        }
        if len(data) >= 14:
            result["cpu_temp"] = float(data[9])
            result["gpu_temp"] = float(data[10])
            result["gpu_power"] = float(data[11])
            result["rdtsc_jitter"] = float(data[12])
            result["hw_timestamp"] = float(data[13])
        else:
            result.update({k: v for k, v in SHADOW_STATE.items() if k not in result})
        return result

    def get_hardware_sensors(self) -> dict:
        if not self.active:
            return SHADOW_HARDWARE.copy()
        try:
            data = (ctypes.c_float * 5)()
            self.lib.get_hardware_state(self.ctx, data)
            return {
                "cpu_temp": float(data[0]),
                "gpu_temp": float(data[1]),
                "gpu_power": float(data[2]),
                "rdtsc_jitter": float(data[3]),
                "hw_timestamp": float(data[4]),
            }
        except Exception:
            return SHADOW_HARDWARE.copy()

    def enable_hardware_log(self, path: str) -> bool:
        if not self.active:
            return False
        try:
            self.lib.enable_hardware_log(self.ctx, path.encode("utf-8"))
            return True
        except Exception as e:
            logger.error("Hardware-Log fehlgeschlagen: %s", e)
            return False

    def disable_hardware_log(self) -> None:
        if self.active:
            try:
                self.lib.disable_hardware_log(self.ctx)
            except Exception:
                pass

    def hardware_log_enabled(self) -> bool:
        if not self.active:
            return False
        try:
            return bool(self.lib.hardware_log_enabled(self.ctx))
        except Exception:
            return False

    def pulse(self, strength: float) -> None:
        if self.active:
            self.lib.pulse_system(self.ctx, ctypes.c_float(strength))

    def apply_cortex_feedback(self, error: bool) -> None:
        if self.active:
            self.lib.apply_cortex_feedback(self.ctx, int(error))

    def set_lorenz_params(self, rho: float, sigma: float, beta: float, reason: float) -> None:
        if not self.active:
            return
        self.lib.set_lorenz_params(
            self.ctx,
            ctypes.c_double(rho), ctypes.c_double(sigma),
            ctypes.c_double(beta), ctypes.c_double(reason),
        )

    def get_lorenz_params(self) -> dict:
        if not self.active:
            return SHADOW_LORENZ.copy()
        out = (ctypes.c_double * 6)()
        self.lib.get_lorenz_params(self.ctx, out)
        return {
            "rho": out[0], "sigma": out[1], "beta": out[2],
            "reason": out[3], "cycle": int(out[4]), "rho_delta": out[5],
        }

    def close(self) -> None:
        if self.active and self.lib:
            self.lib.free_quelibrium(self.ctx)
            self.active = False
