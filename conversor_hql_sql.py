import json
import os
import re
import string
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
import xml.etree.ElementTree as ET


# ==============================================================================
# 1. PERSISTENCIA DE CONFIGURACIÓN
# ==============================================================================
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


# ==============================================================================
# 2. PARSER DE METADATOS HIBERNATE (hibernateConsole.hbm.xml)
# ==============================================================================
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
                    pname = child.attrib.get("name", "id")
                    col = self._get_column_name(child)
                    id_type = child.attrib.get("type", "")
                    id_cls = id_type.split(".")[-1] if id_type else f"{entity_name}_Id"
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

                elif tag == "composite-id":
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

                elif tag == "property":
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

                elif tag == "many-to-one":
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

            self.entities_by_name[entity_name] = entity
            self.entities_by_name[entity_name.lower()] = entity
            self.entities_by_name[full_class] = entity
            self.entities_by_table[table] = entity


# ==============================================================================
# 3. PREPROCESADOR DE CONDICIONALES JAVA (TOMA SIEMPRE LA PRIMERA CONDICIÓN)
# ==============================================================================
def resolve_java_first_if_branch(code: str) -> str:
    """
    Recorre código Java y resuelve cualquier bloque `if (...) { ... } else if ... else ...`
    conservando siempre el cuerpo de la primera condición y descartando ramas alternativas.
    """
    out: list[str] = []
    n = len(code)
    i = 0

    def skip_ws_and_comments(idx: int) -> int:
        while idx < n:
            if code[idx].isspace():
                idx += 1
            elif code.startswith("//", idx):
                nl = code.find("\n", idx)
                idx = n if nl == -1 else nl + 1
            elif code.startswith("/*", idx):
                end_c = code.find("*/", idx + 2)
                idx = n if end_c == -1 else end_c + 2
            else:
                break
        return idx

    def read_balanced(idx: int, open_ch: str, close_ch: str) -> tuple[str, int]:
        depth = 0
        start = idx
        in_str = False
        in_char = False
        while idx < n:
            ch = code[idx]
            if in_str:
                if ch == "\\" and idx + 1 < n:
                    idx += 2
                    continue
                if ch == '"':
                    in_str = False
            elif in_char:
                if ch == "\\" and idx + 1 < n:
                    idx += 2
                    continue
                if ch == "'":
                    in_char = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "'":
                    in_char = True
                elif ch == open_ch:
                    depth += 1
                elif ch == close_ch:
                    depth -= 1
                    if depth == 0:
                        return code[start + 1 : idx], idx + 1
            idx += 1
        return code[start + 1 :], n

    def extract_statement_or_block(idx: int) -> tuple[str, int]:
        idx = skip_ws_and_comments(idx)
        if idx < n and code[idx] == "{":
            return read_balanced(idx, "{", "}")
        # Sentencia simple terminada en ';'
        start = idx
        in_str = False
        while idx < n:
            ch = code[idx]
            if in_str:
                if ch == "\\" and idx + 1 < n:
                    idx += 2
                    continue
                if ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == ";":
                    return code[start : idx + 1], idx + 1
            idx += 1
        return code[start:], n

    while i < n:
        # Preservar literales de cadena y comentarios tal cual
        if code[i] == '"':
            j = i + 1
            while j < n:
                if code[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if code[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(code[i:j])
            i = j
            continue

        if code.startswith("//", i):
            nl = code.find("\n", i)
            j = n if nl == -1 else nl + 1
            out.append(code[i:j])
            i = j
            continue

        if code.startswith("/*", i):
            end_c = code.find("*/", i + 2)
            j = n if end_c == -1 else end_c + 2
            out.append(code[i:j])
            i = j
            continue

        # Detectar palabra clave `if`
        if (
            code.startswith("if", i)
            and (i == 0 or not (code[i - 1].isalnum() or code[i - 1] == "_"))
            and (i + 2 < n and not (code[i + 2].isalnum() or code[i + 2] == "_"))
        ):
            j = skip_ws_and_comments(i + 2)
            if j < n and code[j] == "(":
                _, after_cond = read_balanced(j, "(", ")")
                first_body, cursor = extract_statement_or_block(after_cond)
                # Resolver recursivamente posibles `if` anidados dentro de la primera rama
                out.append("\n" + resolve_java_first_if_branch(first_body) + "\n")

                # Descartar cadenas `else if (...) { ... }` o `else { ... }`
                while True:
                    check = skip_ws_and_comments(cursor)
                    if (
                        code.startswith("else", check)
                        and (check + 4 == n or not (code[check + 4].isalnum() or code[check + 4] == "_"))
                    ):
                        after_else = skip_ws_and_comments(check + 4)
                        if (
                            code.startswith("if", after_else)
                            and (after_else + 2 < n and not (code[after_else + 2].isalnum() or code[after_else + 2] == "_"))
                        ):
                            cond_pos = skip_ws_and_comments(after_else + 2)
                            if cond_pos < n and code[cond_pos] == "(":
                                _, after_else_cond = read_balanced(cond_pos, "(", ")")
                                _, cursor = extract_statement_or_block(after_else_cond)
                                continue
                        _, cursor = extract_statement_or_block(after_else)
                        break
                    else:
                        break

                i = cursor
                continue

        out.append(code[i])
        i += 1

    return "".join(out)


# ==============================================================================
# 4. CONVERSOR HQL (JAVA StringBuilder) -> ORACLE SQL
# ==============================================================================
class HqlToSqlConverter:
    def __init__(self, metadata: HibernateMetadata) -> None:
        self.meta = metadata

    def _extract_hql_string(self, java_code: str) -> str:
        parts: list[str] = []

        # Constructor StringBuilder("...")
        ctor_match = re.search(r'new\s+StringBuilder\s*\(\s*"((?:[^"\\]|\\.)*)"\s*\)', java_code)
        if ctor_match:
            parts.append(ctor_match.group(1))

        # Llamadas .append(...)
        for m in re.finditer(r'\.append\s*\((.*?)\)\s*(?=;|\.)', java_code, flags=re.DOTALL):
            arg_expr = m.group(1)
            literals = re.findall(r'"((?:[^"\\]|\\.)*)"', arg_expr)
            if literals:
                parts.append("".join(literals))

        if parts:
            raw_hql = " ".join(parts)
        else:
            # Si el usuario pegó HQL puro sin StringBuilder
            raw_hql = java_code

        return re.sub(r"\s+", " ", raw_hql).strip()

    def _extract_java_context(self, java_code: str) -> tuple[dict[str, str], list[tuple[str, str]], list[tuple[str, str]], list[str]]:
        """
        Extrae:
        - bound_values: {param_name: sql_literal}
        - add_results: [(hql_expr, alias)]
        - order_bys: [(hql_expr, direction)]
        - bound_lookups: lista de parámetros enlazados explícitamente con bindLookup
        """
        vars_map: dict[str, str] = {}
        bound_values: dict[str, str] = {}
        bound_lookups: list[str] = []
        add_results: list[tuple[str, str]] = []
        order_bys: list[tuple[str, str]] = []

        # 1. Variables Java previas
        # Ej: Account_Id acctId = new Account_Id("3146487875");
        for m in re.finditer(r'\b\w+_Id\s+(\w+)\s*=\s*new\s+\w+_Id\s*\(\s*"([^"]*)"\s*\)', java_code):
            vars_map[m.group(1)] = f"'{m.group(2)}'"

        # Ej: CharacteristicType charType = new CharacteristicType_Id("TARIFA").getEntity();
        for m in re.finditer(r'\b\w+\s+(\w+)\s*=\s*new\s+\w+_Id\s*\(\s*"([^"]*)"\s*\)\.getEntity\s*\(\s*\)', java_code):
            vars_map[m.group(1)] = f"'{m.group(2)}'"

        # Ej: Lookup x = LookupHelper.getLookupInstance(MyLookup.class, "VAL");
        for m in re.finditer(
            r'\b\w+\s+(\w+)\s*=\s*LookupHelper\.getLookupInstance\s*\(\s*[\w.]+\.class\s*,\s*"([^"]*)"\s*\)',
            java_code,
        ):
            vars_map[m.group(1)] = f"'{m.group(2)}'"

        # Ej: String x = "VAL";
        for m in re.finditer(r'\bString\s+(\w+)\s*=\s*"([^"]*)"\s*;', java_code):
            vars_map[m.group(1)] = f"'{m.group(2)}'"

        # Ej: Bool x = Bool.FALSE;
        for m in re.finditer(r'\bBool\s+(\w+)\s*=\s*Bool\.(TRUE|FALSE)\s*;', java_code):
            vars_map[m.group(1)] = "'Y'" if m.group(2) == "TRUE" else "'N'"

        def resolve_arg(token: str) -> str:
            t = token.strip()
            if t in vars_map:
                return vars_map[t]
            if t.startswith('"') and t.endswith('"'):
                return f"'{t[1:-1]}'"
            if "Bool.FALSE" in t or t.lower() == "false":
                return "'N'"
            if "Bool.TRUE" in t or t.lower() == "true":
                return "'Y'"
            lk_inline = re.search(r'LookupHelper\.getLookupInstance\s*\(\s*[\w.]+\.class\s*,\s*"([^"]*)"\s*\)', t)
            if lk_inline:
                return f"'{lk_inline.group(1)}'"
            id_inline = re.search(r'new\s+\w+_Id\s*\(\s*"([^"]*)"\s*\)', t)
            if id_inline:
                return f"'{id_inline.group(1)}'"
            const_inline = re.search(r'\.constants\.(\w+)', t)
            if const_inline:
                return f"'{const_inline.group(1)}'"
            if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
                return t
            return f"'{t}'"

        # 2. Llamadas query.bind...
        for m in re.finditer(r'\.bindLookup\s*\(\s*"([^"]+)"\s*,\s*(.+?)\)\s*;', java_code, flags=re.DOTALL):
            pname = m.group(1)
            bound_values[pname] = resolve_arg(m.group(2))
            bound_lookups.append(pname)

        for m in re.finditer(r'\.bind(?:Id|Entity|Boolean|String|Date|DateTime|BigDecimal|BigInteger|Integer|Object)\s*\(\s*"([^"]+)"\s*,\s*(.+?)\)\s*;', java_code, flags=re.DOTALL):
            pname = m.group(1)
            bound_values[pname] = resolve_arg(m.group(2))

        for m in re.finditer(r'\.bindStringProperty\s*\(\s*"([^"]+)"\s*,\s*[^,]+,\s*(.+?)\)\s*;', java_code, flags=re.DOTALL):
            pname = m.group(1)
            bound_values[pname] = resolve_arg(m.group(2))

        # 3. Llamadas query.addResult(...)
        for m in re.finditer(r'\.addResult\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)\s*;', java_code):
            add_results.append((m.group(1).strip(), m.group(2).strip()))

        # 4. Llamadas query.orderBy(...)
        for m in re.finditer(r'\.orderBy\s*\(\s*"([^"]+)"(?:\s*,\s*Query\.(ASCENDING|DESCENDING))?\s*\)\s*;', java_code):
            direction = "DESC" if m.group(2) == "DESCENDING" else "ASC"
            order_bys.append((m.group(1).strip(), direction))

        return bound_values, add_results, order_bys, bound_lookups

    def _split_hql_clauses(self, hql: str) -> dict[str, str]:
        clauses = {"SELECT": "", "FROM": "", "WHERE": "", "GROUP BY": "", "ORDER BY": ""}
        pattern = re.compile(r"\b(SELECT|FROM|WHERE|GROUP\s+BY|ORDER\s+BY)\b", flags=re.IGNORECASE)
        matches = list(pattern.finditer(hql))
        for idx, m in enumerate(matches):
            key = re.sub(r"\s+", " ", m.group(1).upper())
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(hql)
            clauses[key] = hql[start:end].strip()
        return clauses

    def _map_hql_expr(self, expr: str, alias_map: dict[str, tuple[EntityMeta, str]]) -> tuple[str, PropMeta | None]:
        """
        Convierte una expresión HQL como `acctChar.id.characteristicType` en `ACCTCHAR.CHAR_TYPE_CD`
        y devuelve el PropMeta asociado si existe.
        """
        parts = expr.strip().split(".")
        if len(parts) < 2:
            return expr, None

        hql_alias = parts[0]
        prop_path = ".".join(parts[1:])
        entry = alias_map.get(hql_alias) or alias_map.get(hql_alias.lower())
        if not entry:
            return expr.upper(), None

        entity, sql_alias = entry
        prop_meta = entity.get_by_prop(prop_path)
        if prop_meta:
            return f"{sql_alias}.{prop_meta.column}", prop_meta

        # Fallback si la propiedad no está explícita en el extracto XML
        fallback_col = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", parts[-1]).upper()
        return f"{sql_alias}.{fallback_col}", None

    def convert(self, java_or_hql: str) -> tuple[str, list[str]]:
        notes: list[str] = []
        resolved_code = resolve_java_first_if_branch(java_or_hql)
        if resolved_code != java_or_hql and re.search(r"\bif\s*\(", java_or_hql):
            notes.append("Condicional 'if' detectado: se aplicó la primera condición.")

        hql = self._extract_hql_string(resolved_code)
        if not hql:
            raise ValueError("No se encontró ninguna consulta HQL válida en el texto ingresado.")

        bound_values, add_results, order_bys, bound_lookups = self._extract_java_context(resolved_code)
        clauses = self._split_hql_clauses(hql)

        if not clauses["FROM"]:
            raise ValueError("La consulta HQL debe contener una cláusula FROM.")

        # 1. Procesar FROM -> Entidades y Alias SQL
        alias_map: dict[str, tuple[EntityMeta, str]] = {}
        from_items: list[tuple[EntityMeta, str, str]] = []  # (EntityMeta, hql_alias, sql_alias)

        for raw_item in clauses["FROM"].split(","):
            tokens = [t for t in raw_item.strip().split() if t.upper() != "AS"]
            if not tokens:
                continue
            ent_name = tokens[0]
            hql_alias = tokens[1] if len(tokens) > 1 else ent_name.lower()
            sql_alias = hql_alias.upper()

            entity = self.meta.entities_by_name.get(ent_name) or self.meta.entities_by_name.get(ent_name.lower())
            if not entity:
                raise ValueError(
                    f"La entidad '{ent_name}' no se encuentra mapeada en hibernateConsole.hbm.xml."
                )

            alias_map[hql_alias] = (entity, sql_alias)
            alias_map[hql_alias.lower()] = (entity, sql_alias)
            from_items.append((entity, hql_alias, sql_alias))

        # 2. Procesar WHERE -> Detectar JOINs implícitos y validar Lookups en parámetros
        join_clauses: list[str] = []
        where_conditions: list[str] = []

        if clauses["WHERE"]:
            raw_conds = re.split(r"\bAND\b", clauses["WHERE"], flags=re.IGNORECASE)
            joined_aliases = {from_items[0][2]} if from_items else set()

            for cond in raw_conds:
                c = cond.strip()
                if not c:
                    continue

                # Detectar igualdad entre dos propiedades de alias distintos para promover a JOIN
                eq_match = re.fullmatch(r"([\w.]+)\s*=\s*([\w.]+)", c)
                if eq_match and not eq_match.group(2).startswith(":"):
                    lhs_sql, _ = self._map_hql_expr(eq_match.group(1), alias_map)
                    rhs_sql, _ = self._map_hql_expr(eq_match.group(2), alias_map)
                    lhs_alias = lhs_sql.split(".")[0] if "." in lhs_sql else ""
                    rhs_alias = rhs_sql.split(".")[0] if "." in rhs_sql else ""

                    if lhs_alias and rhs_alias and lhs_alias != rhs_alias and len(from_items) > 1:
                        # Buscar cuál entidad aún no fue unida
                        target_item = next(
                            (it for it in from_items[1:] if it[2] in (lhs_alias, rhs_alias) and it[2] not in joined_aliases),
                            None,
                        )
                        if target_item:
                            t_ent, _, t_sql_alias = target_item
                            joined_aliases.add(t_sql_alias)
                            on_expr = f"{rhs_sql} = {lhs_sql}" if rhs_alias == t_sql_alias else f"{lhs_sql} = {rhs_sql}"
                            join_clauses.append(f"JOIN {t_ent.table} {t_sql_alias}\n        ON {on_expr}")
                            continue

                # Condición con parámetro (:param)
                param_match = re.search(r"([\w.]+)\s*(=|!=|<>|>=|<=|>|<|LIKE|IN)\s*:(\w+)", c, flags=re.IGNORECASE)
                if param_match:
                    lhs_hql = param_match.group(1)
                    op = param_match.group(2).upper()
                    pname = param_match.group(3)
                    lhs_sql, prop_meta = self._map_hql_expr(lhs_hql, alias_map)

                    # Validación de Lookup en Hibernate XML
                    if prop_meta and prop_meta.lookup_class:
                        if pname in bound_lookups:
                            notes.append(
                                f"Lookup '{prop_meta.lookup_class}' validada en Hibernate XML y enlazada para ':{pname}'."
                            )
                        else:
                            notes.append(
                                f"Aviso: La propiedad '{lhs_hql}' tiene asociada la lookup '{prop_meta.lookup_class}' en Hibernate XML."
                            )

                    val_sql = bound_values.get(pname, f"':{pname.upper()}'")
                    where_conditions.append(f"{lhs_sql} {op} {val_sql}")
                    continue

                # Cualquier otra condición (literales, IS NULL, etc.)
                def replace_token(tok_m: re.Match) -> str:
                    tok = tok_m.group(0)
                    if "." in tok and not tok.startswith("'"):
                        mapped, _ = self._map_hql_expr(tok, alias_map)
                        return mapped
                    if tok.startswith(":"):
                        return bound_values.get(tok[1:], f"'{tok[1:].upper()}'")
                    return tok

                converted_cond = re.sub(r":\w+|[\w]+\.[\w.]+", replace_token, c)
                where_conditions.append(converted_cond)

        # 3. Construir SELECT
        select_cols: list[str] = []
        if clauses["SELECT"]:
            for sel_item in clauses["SELECT"].split(","):
                parts = sel_item.strip().split()
                col_sql, _ = self._map_hql_expr(parts[0], alias_map)
                if len(parts) > 1:
                    col_sql = f"{col_sql} {parts[-1].upper()}"
                select_cols.append(col_sql)
        elif add_results:
            for res_expr, _ in add_results:
                col_sql, _ = self._map_hql_expr(res_expr, alias_map)
                if col_sql not in select_cols:
                    select_cols.append(col_sql)
        else:
            first_ent, _, first_alias = from_items[0]
            id_meta = first_ent.get_by_prop("id")
            if id_meta:
                select_cols.append(f"{first_alias}.{id_meta.column}")
            else:
                select_cols.append(f"{first_alias}.*")

        # 4. Construir ORDER BY
        order_cols: list[str] = []
        if clauses["ORDER BY"]:
            for ob in clauses["ORDER BY"].split(","):
                toks = ob.strip().split()
                if toks:
                    col_sql, _ = self._map_hql_expr(toks[0], alias_map)
                    direction = toks[1].upper() if len(toks) > 1 else "ASC"
                    order_cols.append(f"{col_sql} {direction}")
        elif order_bys:
            for ob_expr, direction in order_bys:
                col_sql, _ = self._map_hql_expr(ob_expr, alias_map)
                order_cols.append(f"{col_sql} {direction}")

        # 5. Ensamblar Oracle SQL formateado e indentado
        sql_lines: list[str] = []
        sql_lines.append("SELECT")
        sql_lines.append("    " + ",\n    ".join(select_cols))

        sql_lines.append("FROM")
        base_ent, _, base_alias = from_items[0]
        joined_set = {base_alias}
        for jc in join_clauses:
            for _, _, al in from_items[1:]:
                if f" {al}\n" in jc:
                    joined_set.add(al)

        remaining_from = [f"{ent.table} {al}" for ent, _, al in from_items if al not in joined_set]
        from_block = f"    {base_ent.table} {base_alias}"
        if remaining_from:
            from_block += ",\n    " + ",\n    ".join(remaining_from)
        sql_lines.append(from_block)

        for jc in join_clauses:
            sql_lines.append("    " + jc)

        if where_conditions:
            sql_lines.append("WHERE")
            sql_lines.append("    " + "\n    AND ".join(where_conditions))

        if order_cols:
            sql_lines.append("ORDER BY")
            sql_lines.append("    " + ",\n    ".join(order_cols))

        return "\n".join(sql_lines) + ";", notes


# ==============================================================================
# 5. CONVERSOR ORACLE SQL -> HQL (JAVA StringBuilder)
# ==============================================================================
class SqlToHqlConverter:
    def __init__(self, metadata: HibernateMetadata) -> None:
        self.meta = metadata

    @staticmethod
    def _to_camel_alias(raw_alias: str, entity_name: str, used: set[str]) -> str:
        clean = raw_alias.strip()
        if clean.isupper() and len(clean) > 4 and clean.lower().startswith(entity_name[:4].lower()):
            # Ej: ACCTCHAR -> acctChar
            prefix = entity_name[:4].lower()
            rest = clean[4:].capitalize()
            candidate = prefix + rest
        elif clean:
            candidate = clean.lower()
        else:
            candidate = entity_name[0].lower() + entity_name[1:]

        base = candidate
        idx = 2
        while candidate in used:
            candidate = f"{base}{idx}"
            idx += 1
        used.add(candidate)
        return candidate

    def _split_sql_clauses(self, sql: str) -> dict[str, str]:
        clauses = {"SELECT": "", "FROM": "", "WHERE": "", "GROUP BY": "", "ORDER BY": ""}
        pattern = re.compile(r"\b(SELECT|FROM|WHERE|GROUP\s+BY|ORDER\s+BY)\b", flags=re.IGNORECASE)

        # Ignorar palabras clave dentro de literales '...' o paréntesis
        matches: list[re.Match] = []
        depth = 0
        in_str = False
        str_ranges: list[tuple[int, int]] = []
        start_s = 0
        for i, ch in enumerate(sql):
            if ch == "'":
                if not in_str:
                    in_str = True
                    start_s = i
                else:
                    in_str = False
                    str_ranges.append((start_s, i))

        for m in pattern.finditer(sql):
            pos = m.start()
            if any(s <= pos <= e for s, e in str_ranges):
                continue
            prefix = sql[:pos]
            if prefix.count("(") != prefix.count(")"):
                continue
            matches.append(m)

        for idx, m in enumerate(matches):
            key = re.sub(r"\s+", " ", m.group(1).upper())
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(sql)
            clauses[key] = sql[start:end].strip()

        return clauses

    def _map_sql_col(
        self,
        col_expr: str,
        alias_map: dict[str, tuple[EntityMeta, str]],
        prefer_id_suffix: bool = False,
    ) -> tuple[str, PropMeta | None, EntityMeta | None]:
        parts = col_expr.strip().split(".")
        if len(parts) == 2:
            sql_al = parts[0].upper()
            col_name = parts[1].upper()
            entry = alias_map.get(sql_al)
            if not entry:
                return col_expr, None, None
            entity, hql_al = entry
            prop_meta = entity.get_by_col(col_name)
            if prop_meta:
                path = prop_meta.path
                if prefer_id_suffix and prop_meta.prop_type == "entity" and not path.endswith(".id"):
                    path = f"{path}.id"
                return f"{hql_al}.{path}", prop_meta, entity
            fallback_prop = col_name.lower()
            return f"{hql_al}.{fallback_prop}", None, entity

        # Columna sin prefijo de alias
        col_name = parts[0].upper()
        for _, (entity, hql_al) in alias_map.items():
            prop_meta = entity.get_by_col(col_name)
            if prop_meta:
                path = prop_meta.path
                if prefer_id_suffix and prop_meta.prop_type == "entity" and not path.endswith(".id"):
                    path = f"{path}.id"
                return f"{hql_al}.{path}", prop_meta, entity

        return col_expr, None, None

    def convert(self, raw_sql: str) -> tuple[str, list[str]]:
        notes: list[str] = []
        clean_sql = raw_sql.strip().rstrip(";").strip()
        if not clean_sql:
            raise ValueError("Ingrese una consulta Oracle SQL válida.")

        clauses = self._split_sql_clauses(clean_sql)
        if not clauses["SELECT"] or not clauses["FROM"]:
            raise ValueError("La consulta SQL debe contener las cláusulas SELECT y FROM.")

        # 1. Procesar FROM y JOINs
        alias_map: dict[str, tuple[EntityMeta, str]] = {}
        from_entities: list[tuple[EntityMeta, str]] = []
        join_on_conditions: list[str] = []
        used_hql_aliases: set[str] = set()

        from_part = clauses["FROM"]
        # Separar JOINs explícitos
        join_splits = re.split(
            r"\b(?:INNER\s+|LEFT\s+(?:OUTER\s+)?|RIGHT\s+(?:OUTER\s+)?)?JOIN\b",
            from_part,
            flags=re.IGNORECASE,
        )

        # Primera parte puede tener tablas separadas por coma
        for tbl_item in join_splits[0].split(","):
            toks = [t for t in tbl_item.strip().split() if t.upper() != "AS"]
            if not toks:
                continue
            tbl_name = toks[0].upper()
            sql_al = toks[1].upper() if len(toks) > 1 else tbl_name
            entity = self.meta.entities_by_table.get(tbl_name)
            if not entity:
                raise ValueError(f"La tabla '{tbl_name}' no está mapeada en hibernateConsole.hbm.xml.")
            hql_al = self._to_camel_alias(sql_al, entity.name, used_hql_aliases)
            alias_map[sql_al] = (entity, hql_al)
            from_entities.append((entity, hql_al))

        # Resto son cláusulas JOIN table alias ON ...
        for j_segment in join_splits[1:]:
            on_match = re.split(r"\bON\b", j_segment, maxsplit=1, flags=re.IGNORECASE)
            tbl_toks = [t for t in on_match[0].strip().split() if t.upper() != "AS"]
            if not tbl_toks:
                continue
            tbl_name = tbl_toks[0].upper()
            sql_al = tbl_toks[1].upper() if len(tbl_toks) > 1 else tbl_name
            entity = self.meta.entities_by_table.get(tbl_name)
            if not entity:
                raise ValueError(f"La tabla '{tbl_name}' no está mapeada en hibernateConsole.hbm.xml.")
            hql_al = self._to_camel_alias(sql_al, entity.name, used_hql_aliases)
            alias_map[sql_al] = (entity, hql_al)
            from_entities.append((entity, hql_al))

            if len(on_match) > 1:
                for on_c in re.split(r"\bAND\b", on_match[1], flags=re.IGNORECASE):
                    if on_c.strip():
                        join_on_conditions.append(on_c.strip())

        # 2. Procesar condiciones (JOIN ON + WHERE)
        all_sql_conds: list[str] = list(join_on_conditions)
        if clauses["WHERE"]:
            for wc in re.split(r"\bAND\b", clauses["WHERE"], flags=re.IGNORECASE):
                if wc.strip():
                    all_sql_conds.append(wc.strip())

        hql_where_lines: list[str] = []
        java_pre_decls: list[str] = []
        java_binds: list[str] = []
        used_params: set[str] = set()

        def unique_param(base: str) -> str:
            clean_p = re.sub(r"[^a-zA-Z0-9]", "", base)
            if not clean_p:
                clean_p = "param"
            clean_p = clean_p[0].lower() + clean_p[1:]
            cand = clean_p
            n_idx = 2
            while cand in used_params:
                cand = f"{clean_p}{n_idx}"
                n_idx += 1
            used_params.add(cand)
            return cand

        for cond in all_sql_conds:
            # Igualdad entre dos columnas (Join)
            col_eq = re.fullmatch(r"([\w.]+)\s*=\s*([\w.]+)", cond)
            if col_eq and not col_eq.group(2).startswith("'") and not col_eq.group(2).isdigit():
                lhs_hql, _, _ = self._map_sql_col(col_eq.group(1), alias_map, prefer_id_suffix=True)
                rhs_hql, _, _ = self._map_sql_col(col_eq.group(2), alias_map, prefer_id_suffix=True)
                # Mantener orden natural de entidades del FROM
                first_hql_al = from_entities[0][1]
                if rhs_hql.startswith(f"{first_hql_al}.") and not lhs_hql.startswith(f"{first_hql_al}."):
                    hql_where_lines.append(f"{rhs_hql} = {lhs_hql}")
                else:
                    hql_where_lines.append(f"{lhs_hql} = {rhs_hql}")
                continue

            # Condición con literal: COL OP 'VAL' o COL OP 123
            lit_match = re.fullmatch(
                r"([\w.]+)\s*(=|!=|<>|>=|<=|>|<|LIKE)\s*(?:'([^']*)'|(-?\d+(?:\.\d+)?))",
                cond,
                flags=re.IGNORECASE,
            )
            if lit_match:
                sql_col = lit_match.group(1)
                op = lit_match.group(2).upper()
                str_val = lit_match.group(3)
                num_val = lit_match.group(4)
                raw_val = str_val if str_val is not None else num_val

                hql_path, prop_meta, owner_ent = self._map_sql_col(sql_col, alias_map, prefer_id_suffix=False)
                leaf_name = hql_path.split(".")[-1]
                param_name = unique_param(leaf_name)
                hql_where_lines.append(f"{hql_path} {op} :{param_name}")

                # Generar bind adecuado según metadatos de Hibernate
                if prop_meta and prop_meta.lookup_class:
                    notes.append(
                        f"Lookup '{prop_meta.lookup_class}' detectada en Hibernate XML para '{hql_path}'."
                    )
                    java_binds.append(
                        f'query.bindLookup("{param_name}", LookupHelper.getLookupInstance({prop_meta.lookup_class}.class, "{raw_val}"));'
                    )
                elif prop_meta and prop_meta.prop_type == "boolean":
                    b_const = "Bool.TRUE" if str(raw_val).upper() in ("Y", "T", "1", "TRUE") else "Bool.FALSE"
                    java_binds.append(f'query.bindBoolean("{param_name}", {b_const});')
                elif prop_meta and prop_meta.prop_type == "entity" and prop_meta.target_entity:
                    var_name = unique_param(prop_meta.target_entity)
                    java_pre_decls.append(
                        f'{prop_meta.target_entity} {var_name} = new {prop_meta.target_entity}_Id("{raw_val}").getEntity();'
                    )
                    java_binds.append(f'query.bindEntity("{param_name}", {var_name});')
                elif prop_meta and prop_meta.prop_type == "id" and owner_ent:
                    var_name = unique_param(f"{owner_ent.name}Id")
                    java_pre_decls.append(f'{owner_ent.id_class} {var_name} = new {owner_ent.id_class}("{raw_val}");')
                    java_binds.append(f'query.bindId("{param_name}", {var_name});')
                elif prop_meta and prop_meta.prop_type == "date":
                    java_binds.append(f'query.bindDate("{param_name}", Date.fromIso("{raw_val}"));')
                elif prop_meta and prop_meta.prop_type == "decimal" or num_val is not None:
                    java_binds.append(f'query.bindBigDecimal("{param_name}", new BigDecimal("{raw_val}"));')
                elif owner_ent:
                    prop_ref = hql_path.split(".", 1)[1]
                    java_binds.append(
                        f'query.bindStringProperty("{param_name}", {owner_ent.name}.properties.{prop_ref}, "{raw_val}");'
                    )
                else:
                    java_binds.append(f'query.bindString("{param_name}", "{raw_val}");')
                continue

            # Condiciones como IS NULL / IS NOT NULL
            null_match = re.fullmatch(r"([\w.]+)\s+(IS\s+(?:NOT\s+)?NULL)", cond, flags=re.IGNORECASE)
            if null_match:
                hql_path, _, _ = self._map_sql_col(null_match.group(1), alias_map, prefer_id_suffix=False)
                hql_where_lines.append(f"{hql_path} {null_match.group(2).upper()}")
                continue

            hql_where_lines.append(cond)

        # 3. Procesar SELECT -> addResult
        result_lines: list[str] = []
        generic_type = "SQLRow"
        sel_items = [s.strip() for s in clauses["SELECT"].split(",") if s.strip()]
        for idx, s_item in enumerate(sel_items):
            col_tok = s_item.split()[0]
            hql_path, prop_meta, owner_ent = self._map_sql_col(col_tok, alias_map, prefer_id_suffix=True)
            result_lines.append(f'query.addResult("{hql_path}", "{hql_path}");')
            if idx == 0 and len(sel_items) == 1:
                if prop_meta and prop_meta.prop_type == "id" and owner_ent:
                    generic_type = owner_ent.id_class
                elif prop_meta and prop_meta.id_class:
                    generic_type = prop_meta.id_class

        # 4. Procesar ORDER BY -> query.orderBy
        order_lines: list[str] = []
        if clauses["ORDER BY"]:
            for ob in clauses["ORDER BY"].split(","):
                toks = ob.strip().split()
                if toks:
                    hql_path, _, _ = self._map_hql_col = self._map_sql_col(toks[0], alias_map, prefer_id_suffix=False)
                    q_dir = "Query.DESCENDING" if (len(toks) > 1 and toks[1].upper() == "DESC") else "Query.ASCENDING"
                    order_lines.append(f'query.orderBy("{hql_path}", {q_dir});')

        # 5. Ensamblar código Java con StringBuilder bien indentado
        java_out: list[str] = []
        if java_pre_decls:
            java_out.extend(java_pre_decls)
            java_out.append("")

        from_str = ", ".join(f"{ent.name} {al}" for ent, al in from_entities)
        java_out.append("StringBuilder hql = new StringBuilder();")
        java_out.append(f'hql.append(" FROM {from_str} ")')

        for idx, w_line in enumerate(hql_where_lines):
            prefix = "WHERE" if idx == 0 else "AND"
            is_last = idx == len(hql_where_lines) - 1
            end_semi = ";" if is_last else ""
            java_out.append(f'   .append(" {prefix} {w_line} "){end_semi}')

        if not hql_where_lines:
            java_out[-1] = java_out[-1] + ";"

        java_out.append("")
        java_out.append(f'Query<{generic_type}> query = createQuery(hql.toString(), "");')
        java_out.extend(java_binds)

        if result_lines:
            java_out.append("")
            java_out.extend(result_lines)

        if order_lines:
            java_out.extend(order_lines)

        return "\n".join(java_out), notes


# ==============================================================================
# 6. INTERFAZ GRÁFICA (TKINTER)
# ==============================================================================
class HqlSqlConverterApp:
    TARGET_FILENAME = "hibernateConsole.hbm.xml"

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Conversor HQL (Java) ↔ Oracle SQL - Hibernate")
        self.root.geometry("1040x760")
        self.root.minsize(860, 640)

        self.metadata = HibernateMetadata()
        self.xml_path: str | None = None
        self.searching = False
        self.cancel_search = False

        self._setup_styles()
        self.main_container = ttk.Frame(self.root, padding=16)
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # Verificar si ya existe ubicación guardada en la PC
        saved_path = ConfigManager.load_xml_path()
        if saved_path and self._try_load_hibernate_xml(saved_path, silent=True):
            self._build_converter_screen()
        else:
            self._build_setup_screen()

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 7))

    def _clear_container(self) -> None:
        for widget in self.main_container.winfo_children():
            widget.destroy()

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

    # --------------------------------------------------------------------------
    # PANTALLA 1: LOCALIZACIÓN OBLIGATORIA DE hibernateConsole.hbm.xml
    # --------------------------------------------------------------------------
    def _build_setup_screen(self) -> None:
        self._clear_container()

        frame = ttk.Frame(self.main_container, padding=24)
        frame.place(relx=0.5, rely=0.45, anchor=tk.CENTER)

        ttk.Label(
            frame,
            text="Configuración Inicial Requerida",
            style="Title.TLabel",
        ).pack(pady=(0, 8))

        ttk.Label(
            frame,
            text=(
                f"Para continuar, la aplicación necesita localizar el archivo '{self.TARGET_FILENAME}'.\n"
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

        self.setup_status_var = tk.StringVar(value="Esperando selección o búsqueda del archivo XML...")
        ttk.Label(
            frame,
            textvariable=self.setup_status_var,
            foreground="#333333",
            wraplength=560,
            justify=tk.CENTER,
        ).pack(pady=4)

    def _on_browse_xml(self) -> None:
        file_path = filedialog.askopenfilename(
            title=f"Seleccionar {self.TARGET_FILENAME}",
            filetypes=[("Hibernate XML", "hibernateConsole.hbm.xml *.xml"), ("Todos los archivos", "*.*")],
        )
        if not file_path:
            return
        if self._try_load_hibernate_xml(file_path):
            self._build_converter_screen()

    def _on_cancel_search(self) -> None:
        self.cancel_search = True
        self.setup_status_var.set("Cancelando búsqueda...")

    def _get_search_roots(self) -> list[str]:
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

    def _on_start_global_search(self) -> None:
        if self.searching:
            return
        self.searching = True
        self.cancel_search = False
        self.btn_browse.config(state=tk.DISABLED)
        self.btn_search.config(state=tk.DISABLED)
        self.btn_cancel_search.config(state=tk.NORMAL)
        self.progress.start(12)

        thread = threading.Thread(target=self._global_search_worker, daemon=True)
        thread.start()

    def _global_search_worker(self) -> None:
        target_lower = self.TARGET_FILENAME.lower()
        skip_dirs = {
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
        visited: set[str] = set()
        found_path: str | None = None

        for root_dir in self._get_search_roots():
            if self.cancel_search or found_path:
                break
            for current_root, dirs, files in os.walk(root_dir, onerror=lambda e: None):
                if self.cancel_search:
                    break

                real_root = os.path.realpath(current_root)
                if real_root in visited:
                    dirs[:] = []
                    continue
                visited.add(real_root)

                dirs[:] = [d for d in dirs if d.lower() not in skip_dirs]

                self.root.after(
                    0,
                    lambda p=current_root: self.setup_status_var.set(f"Buscando en: {p}"),
                )

                for fname in files:
                    if fname.lower() == target_lower:
                        found_path = os.path.join(current_root, fname)
                        break

                if found_path:
                    break

        self.root.after(0, lambda: self._on_global_search_finished(found_path))

    def _on_global_search_finished(self, found_path: str | None) -> None:
        self.searching = False
        self.progress.stop()
        self.btn_browse.config(state=tk.NORMAL)
        self.btn_search.config(state=tk.NORMAL)
        self.btn_cancel_search.config(state=tk.DISABLED)

        if self.cancel_search:
            self.setup_status_var.set("Búsqueda cancelada. La aplicación no puede continuar sin el archivo XML.")
            return

        if not found_path:
            self.setup_status_var.set(
                f"No se encontró '{self.TARGET_FILENAME}' en la PC. La aplicación no puede continuar."
            )
            messagebox.showwarning(
                "Archivo no encontrado",
                f"No se encontró '{self.TARGET_FILENAME}' en el equipo.\n"
                "Selecciónelo manualmente para poder continuar.",
            )
            return

        if self._try_load_hibernate_xml(found_path):
            messagebox.showinfo(
                "Archivo encontrado",
                f"Se encontró y cargó correctamente:\n{found_path}",
            )
            self._build_converter_screen()

    # --------------------------------------------------------------------------
    # PANTALLA 2: CONVERSOR HQL <-> SQL
    # --------------------------------------------------------------------------
    def _build_converter_screen(self) -> None:
        self._clear_container()

        # Cabecera con ruta del XML y botón para cambiarlo
        header = ttk.Frame(self.main_container)
        header.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(header, text="Conversor HQL ↔ Oracle SQL", style="Title.TLabel").pack(side=tk.LEFT)

        ttk.Button(
            header,
            text="Cambiar XML Hibernate",
            command=self._build_setup_screen,
        ).pack(side=tk.RIGHT)

        info_bar = ttk.Frame(self.main_container)
        info_bar.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            info_bar,
            text=f"XML activo ({len(self.metadata.entities_by_table)} tablas): {self.xml_path}",
            foreground="#444444",
        ).pack(side=tk.LEFT)

        # Selector de dirección
        mode_frame = ttk.LabelFrame(self.main_container, text="1. Seleccione el tipo de conversión", padding=10)
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

        # Área de entrada
        input_frame = ttk.LabelFrame(self.main_container, text="2. Pegue aquí su consulta", padding=10)
        input_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.input_text = scrolledtext.ScrolledText(
            input_frame,
            height=11,
            font=("Consolas", 10),
            wrap=tk.NONE,
        )
        self.input_text.pack(fill=tk.BOTH, expand=True)

        # Botón CONVERTIR y acciones auxiliares
        action_bar = ttk.Frame(self.main_container)
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

        self.conv_notes_var = tk.StringVar(value="")
        ttk.Label(
            action_bar,
            textvariable=self.conv_notes_var,
            foreground="#1B5E20",
            font=("Segoe UI", 9, "italic"),
        ).pack(side=tk.LEFT, padx=14)

        # Área de salida en texto plano indentado
        output_frame = ttk.LabelFrame(self.main_container, text="3. Resultado (texto plano indentado)", padding=10)
        output_frame.pack(fill=tk.BOTH, expand=True)

        self.output_text = scrolledtext.ScrolledText(
            output_frame,
            height=12,
            font=("Consolas", 10),
            wrap=tk.NONE,
            bg="#F8F9FA",
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)

    def _on_mode_changed(self) -> None:
        self.conv_notes_var.set("")
        self.output_text.delete("1.0", tk.END)

    def _on_clear(self) -> None:
        self.input_text.delete("1.0", tk.END)
        self.output_text.delete("1.0", tk.END)
        self.conv_notes_var.set("")

    def _on_copy_output(self) -> None:
        result = self.output_text.get("1.0", tk.END).strip()
        if result:
            self.root.clipboard_clear()
            self.root.clipboard_append(result)
            self.conv_notes_var.set("Resultado copiado al portapapeles.")

    def _on_convert(self) -> None:
        raw_input = self.input_text.get("1.0", tk.END).strip()
        if not raw_input:
            messagebox.showwarning("Entrada vacía", "Por favor pegue una consulta antes de presionar CONVERTIR.")
            return

        try:
            if self.direction_var.get() == "HQL_TO_SQL":
                converter = HqlToSqlConverter(self.metadata)
            else:
                converter = SqlToHqlConverter(self.metadata)

            result, notes = converter.convert(raw_input)
            self.output_text.delete("1.0", tk.END)
            self.output_text.insert("1.0", result)
            self.conv_notes_var.set(" | ".join(notes) if notes else "Conversión completada correctamente.")
        except Exception as exc:
            messagebox.showerror("Error de conversión", str(exc))


if __name__ == "__main__":
    root = tk.Tk()
    app = HqlSqlConverterApp(root)
    root.mainloop()