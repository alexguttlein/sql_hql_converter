"""Persistencia de la configuración del usuario (ruta del XML de Hibernate)."""
import json
import os
from pathlib import Path


class ConfigManager:
    CONFIG_DIR = Path.home() / ".hql_sql_converter"
    CONFIG_FILE = CONFIG_DIR / "config.json"

    @classmethod
    def load_xml_path(cls) -> str | None:
        if not cls.CONFIG_FILE.exists():
            return None
        try:
            data = json.loads(cls.CONFIG_FILE.read_text(encoding="utf-8"))
            xml_path = data.get("hibernate_xml_path")
            if xml_path and os.path.isfile(xml_path):
                return xml_path
        except Exception:
            return None
        return None

    @classmethod
    def save_xml_path(cls, xml_path: str) -> None:
        cls.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"hibernate_xml_path": os.path.abspath(xml_path)}
        cls.CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
