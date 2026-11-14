"""Pantalla 1: localización obligatoria de hibernateConsole.hbm.xml."""
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from core.xml_finder import XmlFileFinder


class SetupScreen(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        target_filename: str,
        on_xml_selected: Callable[[str, bool], bool],
    ) -> None:
        """
        on_xml_selected(path, show_found_message) -> True si el XML se cargó bien.
        La app principal decide qué pantalla mostrar a continuación.
        """
        super().__init__(parent)
        self.target_filename = target_filename
        self.on_xml_selected = on_xml_selected
        self.finder: XmlFileFinder | None = None
        self.searching = False
        self._build()

    # --------------------------------------------------------------------------
    # Construcción de widgets
    # --------------------------------------------------------------------------
    def _build(self) -> None:
        frame = ttk.Frame(self, padding=24)
        frame.place(relx=0.5, rely=0.45, anchor=tk.CENTER)

        ttk.Label(
            frame,
            text="Configuración Inicial Requerida",
            style="Title.TLabel",
        ).pack(pady=(0, 8))

        ttk.Label(
            frame,
            text=(
                f"Para continuar, la aplicación necesita localizar el archivo '{self.target_filename}'.\n"
                "La ubicación quedará guardada en su PC para los próximos inicios."
            ),
            style="Subtitle.TLabel",
            justify=tk.CENTER,
        ).pack(pady=(0, 22))

        btn_row = ttk.Frame(frame)
        btn_row.pack(pady=8)

        self.btn_browse = ttk.Button(
            btn_row,
            text="Seleccionar archivo manualmente...",
            style="Accent.TButton",
            command=self._on_browse_xml,
        )
        self.btn_browse.pack(side=tk.LEFT, padx=8)

        self.btn_search = ttk.Button(
            btn_row,
            text="Buscar en toda la PC",
            style="Accent.TButton",
            command=self._on_start_global_search,
        )
        self.btn_search.pack(side=tk.LEFT, padx=8)

        self.btn_cancel_search = ttk.Button(
            btn_row,
            text="Cancelar búsqueda",
            command=self._on_cancel_search,
            state=tk.DISABLED,
        )
        self.btn_cancel_search.pack(side=tk.LEFT, padx=8)

        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=480)
        self.progress.pack(pady=(20, 8))

        self.status_var = tk.StringVar(value="Esperando selección o búsqueda del archivo XML...")
        ttk.Label(
            frame,
            textvariable=self.status_var,
            foreground="#333333",
            wraplength=560,
            justify=tk.CENTER,
        ).pack(pady=4)

    # --------------------------------------------------------------------------
    # Selección manual
    # --------------------------------------------------------------------------
    def _on_browse_xml(self) -> None:
        file_path = filedialog.askopenfilename(
            title=f"Seleccionar {self.target_filename}",
            filetypes=[("Hibernate XML", "hibernateConsole.hbm.xml *.xml"), ("Todos los archivos", "*.*")],
        )
        if not file_path:
            return
        self.on_xml_selected(file_path, False)

    # --------------------------------------------------------------------------
    # Búsqueda global (en hilo aparte)
    # --------------------------------------------------------------------------
    def _on_cancel_search(self) -> None:
        if self.finder:
            self.finder.cancel()
        self.status_var.set("Cancelando búsqueda...")

    def _on_start_global_search(self) -> None:
        if self.searching:
            return
        self.searching = True
        self.finder = XmlFileFinder(self.target_filename)
        self.btn_browse.config(state=tk.DISABLED)
        self.btn_search.config(state=tk.DISABLED)
        self.btn_cancel_search.config(state=tk.NORMAL)
        self.progress.start(12)

        threading.Thread(target=self._global_search_worker, daemon=True).start()

    def _global_search_worker(self) -> None:
        def on_progress(path: str) -> None:
            self.after(0, lambda p=path: self.status_var.set(f"Buscando en: {p}"))

        found_path = self.finder.find(on_progress)
        self.after(0, lambda: self._on_global_search_finished(found_path))

    def _on_global_search_finished(self, found_path: str | None) -> None:
        self.searching = False
        self.progress.stop()
        self.btn_browse.config(state=tk.NORMAL)
        self.btn_search.config(state=tk.NORMAL)
        self.btn_cancel_search.config(state=tk.DISABLED)

        if self.finder and self.finder.cancelled:
            self.status_var.set("Búsqueda cancelada. La aplicación no puede continuar sin el archivo XML.")
            return

        if not found_path:
            self.status_var.set(
                f"No se encontró '{self.target_filename}' en la PC. La aplicación no puede continuar."
            )
            messagebox.showwarning(
                "Archivo no encontrado",
                f"No se encontró '{self.target_filename}' en el equipo.\n"
                "Selecciónelo manualmente para poder continuar.",
            )
            return

        self.on_xml_selected(found_path, True)
