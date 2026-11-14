"""Preprocesador de condicionales Java (toma siempre la primera condición)."""


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
