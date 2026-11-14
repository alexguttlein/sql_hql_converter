"""Conversor Oracle SQL -> HQL (Java StringBuilder)."""
import re

from core.metadata import EntityMeta, HibernateMetadata, PropMeta


class SqlToHqlConverter:
    def __init__(self, metadata: HibernateMetadata) -> None:
        self.meta = metadata

    @staticmethod
    def _to_java_date(raw_val: str) -> str:
        """Convierte fecha 'DD/MM/AAAA' o 'AAAA-MM-DD' en new Date(AAAA, MM, DD)."""
        text = str(raw_val)
        m = re.fullmatch(r"\s*(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:\s.*)?", text)  # dd/MM/yyyy
        if m:
            day, month, year = m.groups()
        else:
            m = re.fullmatch(r"\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:[T\s].*)?", text)  # yyyy-MM-dd
            if not m:
                return f'Date.fromIso("{raw_val}")'  # formato desconocido: comportamiento anterior
            year, month, day = m.groups()
        # int() elimina ceros a la izquierda (en Java, 08 sería un octal inválido)
        return f"new Date({int(year)}, {int(month)}, {int(day)})"

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
                    java_binds.append(f'query.bindDate("{param_name}", {self._to_java_date(raw_val)});')
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
