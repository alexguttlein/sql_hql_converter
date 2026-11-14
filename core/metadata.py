"""Modelo y parser de metadatos Hibernate (hibernateConsole.hbm.xml)."""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass
class PropMeta:
    path: str
    column: str
    prop_type: str = "string"  # id, entity, lookup, boolean, date, decimal, string
    target_entity: str | None = None
    id_class: str | None = None
    lookup_class: str | None = None


@dataclass
class EntityMeta:
    name: str
    full_class: str
    table: str
    id_class: str
    prop_to_meta: dict[str, PropMeta] = field(default_factory=dict)
    col_to_meta: dict[str, PropMeta] = field(default_factory=dict)

    def register(self, meta: PropMeta, is_primary_col_mapping: bool = True) -> None:
        self.prop_to_meta[meta.path] = meta
        self.prop_to_meta[meta.path.lower()] = meta
        col_upper = meta.column.upper()
        if is_primary_col_mapping or col_upper not in self.col_to_meta:
            self.col_to_meta[col_upper] = meta

    def get_by_prop(self, prop_path: str) -> PropMeta | None:
        if prop_path in self.prop_to_meta:
            return self.prop_to_meta[prop_path]
        return self.prop_to_meta.get(prop_path.lower())

    def get_by_col(self, col_name: str) -> PropMeta | None:
        return self.col_to_meta.get(col_name.upper())


class HibernateMetadata:
    def __init__(self) -> None:
        self.entities_by_name: dict[str, EntityMeta] = {}
        self.entities_by_table: dict[str, EntityMeta] = {}
        self.typedef_lookups: dict[str, str] = {}

    @staticmethod
    def _strip_ns(tag: str) -> str:
        return tag.split("}", 1)[-1] if "}" in tag else tag

    @staticmethod
    def _short_name(full_name: str) -> str:
        base = full_name.split(".")[-1]
        # Quita sufijos técnicos, en cualquier orden o combinación (ej: Foo_POJO, Foo_Impl, Foo_POJO_Id)
        return re.sub(r"(?:_(?:POJO|Impl|Id))+$", "", base)

    def _get_column_name(self, elem: ET.Element) -> str | None:
        if "column" in elem.attrib and elem.attrib["column"].strip():
            return elem.attrib["column"].strip().upper()
        for child in elem:
            if self._strip_ns(child.tag) == "column" and child.attrib.get("name"):
                return child.attrib["name"].strip().upper()
        return None

    def _extract_type_and_lookup(self, elem: ET.Element) -> tuple[str, str | None]:
        raw_type = elem.attrib.get("type", "").strip()
        lookup_cls = None

        for child in elem:
            if self._strip_ns(child.tag) == "type":
                if child.attrib.get("name"):
                    raw_type = child.attrib["name"].strip()
                for param in child:
                    if self._strip_ns(param.tag) == "param" and param.attrib.get("name") == "lookup":
                        if param.text and param.text.strip():
                            lookup_cls = param.text.strip()

        if raw_type in self.typedef_lookups:
            lookup_cls = self.typedef_lookups[raw_type]

        if not lookup_cls and raw_type.endswith("Lookup"):
            lookup_cls = raw_type

        if lookup_cls:
            return "lookup", lookup_cls.split(".")[-1]

        low = raw_type.lower()
        if "boolean" in low or "yesno" in low or "truefalse" in low:
            return "boolean", None
        if "date" in low or "time" in low:
            return "date", None
        if any(num in low for num in ("decimal", "integer", "long", "numeric", "double", "float")):
            return "decimal", None
        return "string", None

    def load_from_file(self, xml_path: str) -> None:
        self.entities_by_name.clear()
        self.entities_by_table.clear()
        self.typedef_lookups.clear()

        tree = ET.parse(xml_path)
        root = tree.getroot()

        # 1. Detectar typedefs de Lookup si existen
        for elem in root.iter():
            if self._strip_ns(elem.tag) == "typedef":
                tname = elem.attrib.get("name", "")
                for param in elem:
                    if self._strip_ns(param.tag) == "param" and param.attrib.get("name") == "lookup":
                        if tname and param.text:
                            self.typedef_lookups[tname] = param.text.strip()

        # 2. Procesar clases / entidades mapeadas
        for elem in root.iter():
            if self._strip_ns(elem.tag) not in ("class", "subclass", "joined-subclass"):
                continue

            table = elem.attrib.get("table", "").strip().upper()
            full_class = elem.attrib.get("name", "").strip()
            if not table or not full_class:
                continue

            entity_name = self._short_name(full_class)
            entity = EntityMeta(
                name=entity_name,
                full_class=full_class,
                table=table,
                id_class=f"{entity_name}_Id",
            )

            for child in elem:
                tag = self._strip_ns(child.tag)

                if tag == "id":
                    self._parse_id(child, entity, entity_name)
                elif tag == "composite-id":
                    self._parse_composite_id(child, entity)
                elif tag == "property":
                    self._parse_property(child, entity)
                elif tag == "many-to-one":
                    self._parse_many_to_one(child, entity)

            self.entities_by_name[entity_name] = entity
            self.entities_by_name[entity_name.lower()] = entity
            self.entities_by_name[full_class] = entity
            self.entities_by_table[table] = entity

    # --------------------------------------------------------------------------
    # Parsers de cada tipo de nodo dentro de <class>
    # --------------------------------------------------------------------------
    def _parse_id(self, child: ET.Element, entity: EntityMeta, entity_name: str) -> None:
        pname = child.attrib.get("name", "id")
        col = self._get_column_name(child)
        id_type = child.attrib.get("type", "")
        id_cls = (
            re.sub(r"_POJO$", "", id_type.split(".")[-1])
            if id_type else f"{entity_name}_Id"
        )
        entity.id_class = id_cls
        if col:
            entity.register(
                PropMeta(
                    path=pname,
                    column=col,
                    prop_type="id",
                    target_entity=entity_name,
                    id_class=id_cls,
                )
            )

    def _parse_composite_id(self, child: ET.Element, entity: EntityMeta) -> None:
        cid_name = child.attrib.get("name", "id")
        for sub in child:
            sub_tag = self._strip_ns(sub.tag)
            sub_name = sub.attrib.get("name", "")
            col = self._get_column_name(sub)
            if not sub_name or not col:
                continue

            if sub_tag == "key-many-to-one":
                target_ent = self._short_name(sub.attrib.get("class", ""))
                meta_entity = PropMeta(
                    path=f"{cid_name}.{sub_name}",
                    column=col,
                    prop_type="entity",
                    target_entity=target_ent,
                    id_class=f"{target_ent}_Id",
                )
                meta_id = PropMeta(
                    path=f"{cid_name}.{sub_name}.id",
                    column=col,
                    prop_type="entity",
                    target_entity=target_ent,
                    id_class=f"{target_ent}_Id",
                )
                entity.register(meta_entity, is_primary_col_mapping=True)
                entity.register(meta_id, is_primary_col_mapping=False)
            elif sub_tag == "key-property":
                ptype, lookup_cls = self._extract_type_and_lookup(sub)
                entity.register(
                    PropMeta(
                        path=f"{cid_name}.{sub_name}",
                        column=col,
                        prop_type=ptype,
                        lookup_class=lookup_cls,
                    )
                )

    def _parse_property(self, child: ET.Element, entity: EntityMeta) -> None:
        pname = child.attrib.get("name", "")
        col = self._get_column_name(child)
        if pname and col:
            ptype, lookup_cls = self._extract_type_and_lookup(child)
            entity.register(
                PropMeta(
                    path=pname,
                    column=col,
                    prop_type=ptype,
                    lookup_class=lookup_cls,
                )
            )

    def _parse_many_to_one(self, child: ET.Element, entity: EntityMeta) -> None:
        pname = child.attrib.get("name", "")
        col = self._get_column_name(child)
        target_ent = self._short_name(child.attrib.get("class", ""))
        if pname and col:
            meta_entity = PropMeta(
                path=pname,
                column=col,
                prop_type="entity",
                target_entity=target_ent,
                id_class=f"{target_ent}_Id",
            )
            meta_id = PropMeta(
                path=f"{pname}.id",
                column=col,
                prop_type="entity",
                target_entity=target_ent,
                id_class=f"{target_ent}_Id",
            )
            entity.register(meta_entity, is_primary_col_mapping=True)
            entity.register(meta_id, is_primary_col_mapping=False)
