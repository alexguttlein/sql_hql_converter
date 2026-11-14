"""Ventana principal: orquesta la carga del XML y la navegación entre pantallas."""
import os
import tkinter as tk
from tkinter import messagebox, ttk

from core.config_manager import ConfigManager
from core.metadata import HibernateMetadata
from ui.converter_screen import ConverterScreen
from ui.setup_screen import SetupScreen


class HqlSqlConverterApp:
    TARGET_FILENAME = "hibernateConsole.hbm.xml"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Conversor HQL (Java) ↔ Oracle SQL - Hibernate")
        self.root.geometry("1040x760")
        self.root.minsize(860, 640)

        self.metadata = HibernateMetadata()
        self.xml_path: str | None = None

        self._setup_styles()
        self.main_container = ttk.Frame(self.root, padding=16)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # Verificar si ya existe ubicación guardada en la PC
        saved_path = ConfigManager.load_xml_path()
        if saved_path and self._try_load_hibernate_xml(saved_path, silent=True):
            self.show_converter_screen()
        else:
            self.show_setup_screen()

    # --------------------------------------------------------------------------
    # Estilos
    # --------------------------------------------------------------------------
    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 7))

    # --------------------------------------------------------------------------
    # Navegación entre pantallas
    # --------------------------------------------------------------------------
    def _clear_container(self) -> None:
        for widget in self.main_container.winfo_children():
            widget.destroy()

    def show_setup_screen(self) -> None:
        self._clear_container()
        SetupScreen(
            self.main_container,
            target_filename=self.TARGET_FILENAME,
            on_xml_selected=self._on_xml_selected,
        ).pack(fill=tk.BOTH, expand=True)

    def show_converter_screen(self) -> None:
        self._clear_container()
        ConverterScreen(
            self.main_container,
            metadata=self.metadata,
            xml_path=self.xml_path,
            on_change_xml=self.show_setup_screen,
        ).pack(fill=tk.BOTH, expand=True)

    # --------------------------------------------------------------------------
    # Carga del XML
    # --------------------------------------------------------------------------
    def _on_xml_selected(self, path: str, show_found_message: bool) -> bool:
        if not self._try_load_hibernate_xml(path):
            return False
        if show_found_message:
            messagebox.showinfo(
                "Archivo encontrado",
                f"Se encontró y cargó correctamente:\n{path}",
            )
        self.show_converter_screen()
        return True

    def _try_load_hibernate_xml(self, path: str, silent: bool = False) -> bool:
        try:
            self.metadata.load_from_file(path)
            if not self.metadata.entities_by_table:
                raise ValueError("El archivo XML no contiene mapeos <class table='...'> válidos.")
            self.xml_path = os.path.abspath(path)
            ConfigManager.save_xml_path(self.xml_path)
            return True
        except Exception as exc:
            if not silent:
                messagebox.showerror(
                    "Error al leer XML de Hibernate",
                    f"No se pudo procesar '{path}':\n\n{exc}",
                )
            return False
