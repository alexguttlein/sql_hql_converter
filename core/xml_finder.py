"""Búsqueda del archivo hibernateConsole.hbm.xml en el disco (sin dependencias de UI)."""
import os
import string
import threading
from pathlib import Path
from typing import Callable


class XmlFileFinder:
    SKIP_DIRS = {
        "windows",
        "$recycle.bin",
        "system volume information",
        "proc",
        "sys",
        "dev",
        "run",
        "node_modules",
        ".git",
    }

    def __init__(self, target_filename: str) -> None:
        self.target_lower = target_filename.lower()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @staticmethod
    def get_search_roots() -> list[str]:
        roots: list[str] = []
        home = str(Path.home())
        if os.path.exists(home):
            roots.append(home)

        if os.name == "nt":
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive) and drive not in roots:
                    roots.append(drive)
        else:
            if "/" not in roots:
                roots.append("/")
        return roots

    def find(self, on_progress: Callable[[str], None] | None = None) -> str | None:
        """Recorre el disco y devuelve la ruta del archivo, o None si no existe o se canceló."""
        visited: set[str] = set()

        for root_dir in self.get_search_roots():
            if self.cancelled:
                return None
            for current_root, dirs, files in os.walk(root_dir, onerror=lambda e: None):
                if self.cancelled:
                    return None

                real_root = os.path.realpath(current_root)
                if real_root in visited:
                    dirs[:] = []
                    continue
                visited.add(real_root)

                dirs[:] = [d for d in dirs if d.lower() not in self.SKIP_DIRS]

                if on_progress:
                    on_progress(current_root)

                for fname in files:
                    if fname.lower() == self.target_lower:
                        return os.path.join(current_root, fname)
        return None
