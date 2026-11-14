"""Punto de entrada. Ejecutar con:  python main.py"""
import tkinter as tk

from ui.app import HqlSqlConverterApp


def main() -> None:
    root = tk.Tk()
    HqlSqlConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
