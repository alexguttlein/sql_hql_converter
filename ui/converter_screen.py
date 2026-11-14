"""Pantalla 2: conversor HQL <-> SQL."""
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable

from converters.hql_to_sql import HqlToSqlConverter
from converters.sql_to_hql import SqlToHqlConverter
from core.metadata import HibernateMetadata


class ConverterScreen(ttk.Frame):
    CONVERTERS = {
        "HQL_TO_SQL": HqlToSqlConverter,
        "SQL_TO_HQL": SqlToHqlConverter,
    }

    def __init__(
        self,
        parent: tk.Misc,
        metadata: HibernateMetadata,
        xml_path: str,
        on_change_xml: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.metadata = metadata
        self.xml_path = xml_path
        self.on_change_xml = on_change_xml
        self._build()

    # --------------------------------------------------------------------------
    # Construcción de widgets
    # --------------------------------------------------------------------------
    def _build(self) -> None:
        self._build_header()
        self._build_mode_selector()
        self._build_input_area()
        self._build_action_bar()
        self._build_output_area()

    def _build_header(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(header, text="Conversor HQL ↔ Oracle SQL", style="Title.TLabel").pack(side=tk.LEFT)

        ttk.Button(
            header,
            text="Cambiar XML Hibernate",
            command=self.on_change_xml,
        ).pack(side=tk.RIGHT)

        info_bar = ttk.Frame(self)
        info_bar.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            info_bar,
            text=f"XML activo ({len(self.metadata.entities_by_table)} tablas): {self.xml_path}",
            foreground="#444444",
        ).pack(side=tk.LEFT)

    def _build_mode_selector(self) -> None:
        mode_frame = ttk.LabelFrame(self, text="1. Seleccione el tipo de conversión", padding=10)
        mode_frame.pack(fill=tk.X, pady=(0, 10))

        self.direction_var = tk.StringVar(value="HQL_TO_SQL")

        ttk.Radiobutton(
            mode_frame,
            text="HQL (Java StringBuilder)  →  Oracle SQL",
            variable=self.direction_var,
            value="HQL_TO_SQL",
            command=self._on_mode_changed,
        ).pack(side=tk.LEFT, padx=(4, 24))

        ttk.Radiobutton(
            mode_frame,
            text="Oracle SQL  →  HQL (Java StringBuilder)",
            variable=self.direction_var,
            value="SQL_TO_HQL",
            command=self._on_mode_changed,
        ).pack(side=tk.LEFT, padx=4)

    def _build_input_area(self) -> None:
        input_frame = ttk.LabelFrame(self, text="2. Pegue aquí su consulta", padding=10)
        input_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.input_text = scrolledtext.ScrolledText(
            input_frame,
            height=11,
            font=("Consolas", 10),
            wrap=tk.NONE,
        )
        self.input_text.pack(fill=tk.BOTH, expand=True)

    def _build_action_bar(self) -> None:
        action_bar = ttk.Frame(self)
        action_bar.pack(fill=tk.X, pady=(0, 10))

        ttk.Button(
            action_bar,
            text="CONVERTIR",
            style="Accent.TButton",
            command=self._on_convert,
        ).pack(side=tk.LEFT)

        ttk.Button(
            action_bar,
            text="Limpiar",
            command=self._on_clear,
        ).pack(side=tk.LEFT, padx=8)

        ttk.Button(
            action_bar,
            text="Copiar resultado",
            command=self._on_copy_output,
        ).pack(side=tk.RIGHT)

        self.notes_var = tk.StringVar(value="")
        ttk.Label(
            action_bar,
            textvariable=self.notes_var,
            foreground="#1B5E20",
            font=("Segoe UI", 9, "italic"),
        ).pack(side=tk.LEFT, padx=14)

    def _build_output_area(self) -> None:
        output_frame = ttk.LabelFrame(self, text="3. Resultado (texto plano indentado)", padding=10)
        output_frame.pack(fill=tk.BOTH, expand=True)

        self.output_text = scrolledtext.ScrolledText(
            output_frame,
            height=12,
            font=("Consolas", 10),
            wrap=tk.NONE,
            bg="#F8F9FA",
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)

    # --------------------------------------------------------------------------
    # Eventos
    # --------------------------------------------------------------------------
    def _on_mode_changed(self) -> None:
        self.notes_var.set("")
        self.output_text.delete("1.0", tk.END)

    def _on_clear(self) -> None:
        self.input_text.delete("1.0", tk.END)
        self.output_text.delete("1.0", tk.END)
        self.notes_var.set("")

    def _on_copy_output(self) -> None:
        result = self.output_text.get("1.0", tk.END).strip()
        if result:
            self.clipboard_clear()
            self.clipboard_append(result)
            self.notes_var.set("Resultado copiado al portapapeles.")

    def _on_convert(self) -> None:
        raw_input = self.input_text.get("1.0", tk.END).strip()
        if not raw_input:
            messagebox.showwarning("Entrada vacía", "Por favor pegue una consulta antes de presionar CONVERTIR.")
            return

        try:
            converter_cls = self.CONVERTERS[self.direction_var.get()]
            converter = converter_cls(self.metadata)

            result, notes = converter.convert(raw_input)
            self.output_text.delete("1.0", tk.END)
            self.output_text.insert("1.0", result)
            self.notes_var.set(" | ".join(notes) if notes else "Conversión completada correctamente.")
        except Exception as exc:
            messagebox.showerror("Error de conversión", str(exc))
