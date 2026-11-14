"""Conversor Oracle SQL -> HQL (Java StringBuilder)."""
import re

from core.metadata import EntityMeta, HibernateMetadata, PropMeta


class SqlToHqlConverter:
    def __init__(self, metadata: HibernateMetadata) -> None:
        self.meta = metadata

    @staticmethod
    def _to_java_date(raw_val: str, date_mask: str | None = None) -> str:
        """
        Convierte una fecha SQL en new Date(año, mes, día).
        - Con máscara de TO_DATE (ej: 'DD/MM/YYYY'), usa el orden indicado por la máscara.
        - Sin máscara, soporta dd/MM/yyyy y yyyy-MM-dd.
        """
        text = str(raw_val)

        if date_mask:
            tokens = re.findall(r"YYYY|MM|DD", date_mask.upper())
            digits = re.findall(r"\d+", text)
            if sorted(tokens) == ["DD", "MM", "YYYY"] and len(digits) >= 3:
                parts = dict(zip(tokens, digits))
                return f"new Date({int(parts['YYYY'])}, {int(parts['MM'])}, {int(parts['DD'])})"

        m = re.fullmatch(r"\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s.*)?", text)  # dd/MM/yyyy
        if m:
            day, month, year = m.groups()
        else:
            m = re.fullmatch(r"\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:[T\s].*)?", text)  # yyyy-MM-dd
            if not m:
                return f'Date.fromIso("{raw_val}")'  # formato desconocido: comportamiento anterior
            year, month, day = m.groups()
        return f"new Date({int(year)}, {int(month)}, {int(day)})"


    SYSDATE_EXPR = "getProcessDateTime().getDate()"
    # Valores SQL soportados: 'texto', número, TO_DATE(...), SYSDATE / TRUNC(SYSDATE) / CURRENT_DATE (+/- días)
    SQL_VALUE_PATTERN = (
        r"'[^']*'"
        r"|-?\d+(?:\.\d+)?"
        r"|TO_DATE\s*\(\s*'[^']*'\s*(?:,\s*'[^']*'\s*)?\)"
        r"|(?:TRUNC\s*\(\s*SYSDATE\s*\)|SYSDATE|CURRENT_DATE)(?:\s*[+-]\s*\d+)?"
    )

    @staticmethod
    def _split_conditions(text: str) -> list[str]:
        """Separa por AND sin romper el AND propio de 'BETWEEN x AND y'."""
        protected = re.sub(
            r"(\bBETWEEN\b\s+.+?)\s+AND\s+",
            lambda m: f"{m.group(1)} __BETWEEN_AND__ ",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        parts = re.split(r"\bAND\b", protected, flags=re.IGNORECASE)
        return [p.replace("__BETWEEN_AND__", "AND").strip() for p in parts if p.strip()]

    @staticmethod
    def _parse_sql_value(token: str) -> tuple[str, str, str | None]:
        """Devuelve (tipo, valor, máscara). tipo: str | num | date | sysdate (en sysdate, valor = días de desfase)."""
        t = token.strip()
        m = re.fullmatch(r"'([^']*)'", t)
        if m:
            return "str", m.group(1), None
        if re.fullmatch(r"-?\d+(?:\.\d+)?", t):
            return "num", t, None
        m = re.fullmatch(r"TO_DATE\s*\(\s*'([^']*)'\s*(?:,\s*'([^']*)'\s*)?\)", t, flags=re.IGNORECASE)
        if m:
            return "date", m.group(1), m.group(2)
        m = re.fullmatch(
            r"(?:TRUNC\s*\(\s*SYSDATE\s*\)|SYSDATE|CURRENT_DATE)(?:\s*([+-])\s*(\d+))?",
            t,
            flags=re.IGNORECASE,
        )
        if m:
            days = int(m.group(2)) if m.group(2) else 0
            return "sysdate", str(-days if m.group(1) == "-" else days), None
        return "str", t, None

    @staticmethod
    def _split_values(text: str) -> list[str]:
        """Separa una lista 'a, b, c' por comas de primer nivel (respeta comillas y paréntesis)."""
        items: list[str] = []
        buf: list[str] = []
        depth = 0
        in_str = False
        for ch in text:
            if ch == "'":
                in_str = not in_str
            elif not in_str:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "," and depth == 0:
                    items.append("".join(buf).strip())
                    buf = []
                    continue
            buf.append(ch)
        items.append("".join(buf).strip())
        return [i for i in items if i]

    def _java_date_expr(self, kind: str, raw_val: str, date_mask: str | None) -> str:
        if kind == "sysdate":
            days = int(raw_val)
            return self.SYSDATE_EXPR if days == 0 else f"{self.SYSDATE_EXPR}.addDays({days})"
        return self._to_java_date(raw_val, date_mask)


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
                for on_c in self._split_conditions(on_match[1]):
                    if on_c.strip():
                        join_on_conditions.append(on_c.strip())

        # 2. Procesar condiciones (JOIN ON + WHERE)
        all_sql_conds: list[str] = list(join_on_conditions)
        if clauses["WHERE"]:
            for wc in self._split_conditions(clauses["WHERE"]):
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

        def bind_value(param_name, hql_path, prop_meta, owner_ent, kind, raw_val, date_mask) -> None:
            """Genera el bind Java adecuado según metadatos de Hibernate y el tipo de valor SQL."""
            if prop_meta and prop_meta.lookup_class:
                lookup_note = f"Lookup '{prop_meta.lookup_class}' detectada en Hibernate XML para '{hql_path}'."
                if lookup_note not in notes:
                    notes.append(lookup_note)
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
            elif (prop_meta and prop_meta.prop_type == "date") or kind in ("date", "sysdate"):
                java_binds.append(f'query.bindDate("{param_name}", {self._java_date_expr(kind, raw_val, date_mask)});')
            elif (prop_meta and prop_meta.prop_type == "decimal") or kind == "num":
                java_binds.append(f'query.bindBigDecimal("{param_name}", new BigDecimal("{raw_val}"));')
            elif owner_ent:
                prop_ref = hql_path.split(".", 1)[1]
                java_binds.append(
                    f'query.bindStringProperty("{param_name}", {owner_ent.name}.properties.{prop_ref}, "{raw_val}");'
                )
            else:
                java_binds.append(f'query.bindString("{param_name}", "{raw_val}");')

        for cond in all_sql_conds:
            # Igualdad entre dos columnas (Join)
            col_eq = re.fullmatch(r"([\w.]+)\s*=\s*([\w.]+)", cond)
            if (
                col_eq
                and not col_eq.group(2).startswith("'")
                and not col_eq.group(2).isdigit()
                and col_eq.group(2).upper() not in ("SYSDATE", "CURRENT_DATE")
            ):
                lhs_hql, _, _ = self._map_sql_col(col_eq.group(1), alias_map, prefer_id_suffix=True)
                rhs_hql, _, _ = self._map_sql_col(col_eq.group(2), alias_map, prefer_id_suffix=True)
                # Mantener orden natural de entidades del FROM
                first_hql_al = from_entities[0][1]
                if rhs_hql.startswith(f"{first_hql_al}.") and not lhs_hql.startswith(f"{first_hql_al}."):
                    hql_where_lines.append(f"{rhs_hql} = {lhs_hql}")
                else:
                    hql_where_lines.append(f"{lhs_hql} = {rhs_hql}")
                continue

            # Condición con literal: COL OP 'VAL' | 123 | TO_DATE(...) | SYSDATE [+/- n]
            lit_match = re.fullmatch(
                rf"([\w.]+)\s*(=|!=|<>|>=|<=|>|<|LIKE)\s*({self.SQL_VALUE_PATTERN})",
                cond,
                flags=re.IGNORECASE,
            )
            if lit_match:
                op = lit_match.group(2).upper()
                kind, raw_val, date_mask = self._parse_sql_value(lit_match.group(3))

                hql_path, prop_meta, owner_ent = self._map_sql_col(lit_match.group(1), alias_map, prefer_id_suffix=False)
                param_name = unique_param(hql_path.split(".")[-1])
                hql_where_lines.append(f"{hql_path} {op} :{param_name}")
                bind_value(param_name, hql_path, prop_meta, owner_ent, kind, raw_val, date_mask)
                continue

            # Condición BETWEEN: COL [NOT] BETWEEN v1 AND v2
            between_match = re.fullmatch(
                rf"([\w.]+)\s+(NOT\s+)?BETWEEN\s+({self.SQL_VALUE_PATTERN})\s+AND\s+({self.SQL_VALUE_PATTERN})",
                cond,
                flags=re.IGNORECASE,
            )
            if between_match:
                negation = "NOT " if between_match.group(2) else ""
                hql_path, prop_meta, owner_ent = self._map_sql_col(between_match.group(1), alias_map, prefer_id_suffix=False)
                leaf_name = hql_path.split(".")[-1]
                p_from = unique_param(f"{leaf_name}From")
                p_to = unique_param(f"{leaf_name}To")
                hql_where_lines.append(f"{hql_path} {negation}BETWEEN :{p_from} AND :{p_to}")
                for p_name, token in ((p_from, between_match.group(3)), (p_to, between_match.group(4))):
                    kind, raw_val, date_mask = self._parse_sql_value(token)
                    bind_value(p_name, hql_path, prop_meta, owner_ent, kind, raw_val, date_mask)
                continue

            # Condición IN: COL [NOT] IN (v1, v2, ...)  (las subconsultas IN (SELECT ...) no se transforman)
            in_match = re.fullmatch(r"([\w.]+)\s+(NOT\s+)?IN\s*\((.*)\)", cond, flags=re.IGNORECASE | re.DOTALL)
            in_values = self._split_values(in_match.group(3)) if in_match else []
            if in_values and not re.match(r"\s*SELECT\b", in_match.group(3), flags=re.IGNORECASE):
                negation = "NOT " if in_match.group(2) else ""
                hql_path, prop_meta, owner_ent = self._map_sql_col(in_match.group(1), alias_map, prefer_id_suffix=False)
                leaf_name = hql_path.split(".")[-1]
                in_params: list[str] = []
                for token in in_values:
                    kind, raw_val, date_mask = self._parse_sql_value(token)
                    p_name = unique_param(leaf_name)
                    in_params.append(p_name)
                    bind_value(p_name, hql_path, prop_meta, owner_ent, kind, raw_val, date_mask)
                hql_where_lines.append(f"{hql_path} {negation}IN ({', '.join(':' + p for p in in_params)})")
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
                    hql_path, _, _ = self._map_sql_col(toks[0], alias_map, prefer_id_suffix=False)
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
