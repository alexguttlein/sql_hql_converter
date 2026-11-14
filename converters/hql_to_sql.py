"""Conversor HQL (Java StringBuilder) -> Oracle SQL."""
import re

from core.java_preprocessor import resolve_java_first_if_branch
from core.metadata import EntityMeta, HibernateMetadata, PropMeta


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
            
            # new Date(año, mes, día) -> TO_DATE('dd/MM/yyyy','DD/MM/YYYY')
            date_inline = re.fullmatch(r"new\s+Date\s*\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)", t)
            if date_inline:
                y, mo, d = date_inline.groups()
                return f"TO_DATE('{int(d):02d}/{int(mo):02d}/{y}','DD/MM/YYYY')"

            # getProcessDateTime().getDate() [.addDays(n)] -> SYSDATE [+/- n]
            sysdate_inline = re.fullmatch(
                r"getProcessDateTime\s*\(\s*\)\s*\.getDate\s*\(\s*\)(?:\s*\.addDays\s*\(\s*(-?\d+)\s*\))?", t
            )
            if sysdate_inline:
                n = int(sysdate_inline.group(1) or 0)
                return "SYSDATE" if n == 0 else f"SYSDATE {'+' if n > 0 else '-'} {abs(n)}"
            
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
            protected_where = re.sub(
                r"(\bBETWEEN\b\s+:\w+)\s+AND\s+",
                lambda m: f"{m.group(1)} __BETWEEN_AND__ ",
                clauses["WHERE"],
                flags=re.IGNORECASE,
            )
            raw_conds = [
                p.replace("__BETWEEN_AND__", "AND")
                for p in re.split(r"\bAND\b", protected_where, flags=re.IGNORECASE)
            ]
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

                # Condición BETWEEN :p1 AND :p2
                between_match = re.fullmatch(
                    r"([\w.]+)\s+(NOT\s+)?BETWEEN\s+:(\w+)\s+AND\s+:(\w+)", c, flags=re.IGNORECASE
                )
                if between_match:
                    lhs_sql, _ = self._map_hql_expr(between_match.group(1), alias_map)
                    neg = "NOT " if between_match.group(2) else ""
                    v_from = bound_values.get(between_match.group(3), f"':{between_match.group(3).upper()}'")
                    v_to = bound_values.get(between_match.group(4), f"':{between_match.group(4).upper()}'")
                    where_conditions.append(f"{lhs_sql} {neg}BETWEEN {v_from} AND {v_to}")
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
