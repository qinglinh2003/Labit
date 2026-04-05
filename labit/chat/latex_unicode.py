"""Convert LaTeX math notation to Unicode approximations for terminal display.

Architecture:
- **Symbol conversion** is delegated to ``unicodeit`` (covers hundreds of
  LaTeX commands: Greek, operators, arrows, accents, mathbb, mathcal, …).
- **Structural commands** (frac, sqrt, matrices, cases, aligned, delimiters,
  xrightarrow, stackrel, etc.) are handled here because they require
  argument parsing that unicodeit does not do.
- **Math block detection** (``$$...$$`` and ``$...$``) and formatting
  (blockquote vs code-fence) is handled here.
"""

from __future__ import annotations

import re

import unicodeit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _brace_arg(tex: str, pos: int) -> tuple[str, int]:
    r"""Extract a brace-delimited argument ``{...}`` starting at *pos*.

    If *pos* doesn't point at ``{``, return the next single token
    (a ``\command`` or one character).
    """
    if pos >= len(tex):
        return ("", pos)
    if tex[pos] != "{":
        if tex[pos] == "\\" and pos + 1 < len(tex):
            j = pos + 1
            if tex[j].isalpha():
                while j < len(tex) and tex[j].isalpha():
                    j += 1
                return (tex[pos:j], j)
            return (tex[pos : pos + 2], pos + 2)
        return (tex[pos], pos + 1)
    depth = 0
    start = pos + 1
    for i in range(pos, len(tex)):
        if tex[i] == "{":
            depth += 1
        elif tex[i] == "}":
            depth -= 1
            if depth == 0:
                return (tex[start:i], i + 1)
    return (tex[start:], len(tex))


def _opt_arg(tex: str, pos: int) -> tuple[str | None, int]:
    """Extract an optional ``[...]`` argument. Returns (None, pos) if absent."""
    if pos < len(tex) and tex[pos] == "[":
        close = tex.find("]", pos)
        if close != -1:
            return (tex[pos + 1 : close], close + 1)
    return (None, pos)


def _ucit(tex: str) -> str:
    """Run unicodeit on a small LaTeX fragment (single command / symbol)."""
    return unicodeit.replace(tex)


# ---------------------------------------------------------------------------
# Matrix / environment rendering
# ---------------------------------------------------------------------------

_MATRIX_DELIMS: dict[str, tuple[str, str, str, str, str, str]] = {
    "pmatrix":  ("⎛", "⎜", "⎝", "⎞", "⎟", "⎠"),
    "bmatrix":  ("⎡", "⎢", "⎣", "⎤", "⎥", "⎦"),
    "vmatrix":  ("│", "│", "│", "│", "│", "│"),
    "Vmatrix":  ("‖", "‖", "‖", "‖", "‖", "‖"),
    "Bmatrix":  ("⎧", "⎪", "⎩", "⎫", "⎪", "⎭"),
    "matrix":   (" ", " ", " ", " ", " ", " "),
    "smallmatrix": (" ", " ", " ", " ", " ", " "),
}


def _render_matrix(env_name: str, body: str) -> str:
    raw_rows = re.split(r"\\\\(?:\s*\[[^\]]*\])?", body)
    rows: list[list[str]] = []
    for raw in raw_rows:
        raw = raw.strip()
        if not raw:
            continue
        rows.append([_convert(c.strip()) for c in raw.split("&")])
    if not rows:
        return ""
    n_cols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < n_cols:
            r.append("")
    widths = [max(len(r[c]) for r in rows) for c in range(n_cols)]
    delims = _MATRIX_DELIMS.get(env_name, _MATRIX_DELIMS["pmatrix"])
    tl, ml, bl, tr, mr, br = delims
    n = len(rows)
    lines: list[str] = []
    for idx, row in enumerate(rows):
        padded = "  ".join(cell.ljust(widths[c]) for c, cell in enumerate(row))
        if n == 1:
            l, r = tl, tr
        elif idx == 0:
            l, r = tl, tr
        elif idx == n - 1:
            l, r = bl, br
        else:
            l, r = ml, mr
        lines.append(f"{l} {padded} {r}")
    return "\n".join(lines)


def _render_environment(env_name: str, body: str) -> str:
    if env_name in _MATRIX_DELIMS:
        return _render_matrix(env_name, body)
    if env_name in ("cases", "rcases"):
        raw_rows = re.split(r"\\\\(?:\s*\[[^\]]*\])?", body)
        lines: list[str] = []
        for raw in raw_rows:
            raw = raw.strip()
            if not raw:
                continue
            parts = raw.split("&", 1)
            expr = _convert(parts[0].strip())
            cond = _convert(parts[1].strip()) if len(parts) > 1 else ""
            lines.append(f"{expr}  {cond}" if cond else expr)
        if not lines:
            return ""
        brackets = {"cases": ("⎧", "⎨", "⎩"), "rcases": (" ", " ", " ")}
        top, mid, bot = brackets[env_name]
        result: list[str] = []
        for idx, line in enumerate(lines):
            if idx == 0:
                b = top
            elif idx == len(lines) - 1:
                b = bot
            else:
                b = mid
            result.append(f"{b} {line}")
        return "\n".join(result)
    if env_name in ("aligned", "align", "align*", "gathered", "split"):
        raw_rows = re.split(r"\\\\(?:\s*\[[^\]]*\])?", body)
        return "\n".join(
            _convert(raw.replace("&", " ").strip())
            for raw in raw_rows
            if raw.strip()
        )
    return _convert(body)


# ---------------------------------------------------------------------------
# Structural commands — things unicodeit can't handle
# ---------------------------------------------------------------------------

# Commands that take {arg} and produce an arrow with annotation.
_ARROW_COMMANDS: dict[str, str] = {
    "xrightarrow": "→", "xleftarrow": "←", "xmapsto": "↦",
    "xhookrightarrow": "↪", "xhookleftarrow": "↩",
    "xlongrightarrow": "⟶", "xlongleftarrow": "⟵",
}

# Delimiter sizing commands — just pass through to the delimiter itself.
_SIZING_CMDS = frozenset({
    "left", "right",
    "big", "Big", "bigg", "Bigg",
    "bigl", "bigr", "Bigl", "Bigr",
    "biggl", "biggr", "Biggl", "Biggr",
    "bigm", "Bigm", "biggm", "Biggm",
    "middle",
})

# Standard math operator names — render as plain text, never pass to unicodeit
# (unicodeit mangles some of these, e.g. \log → łog because \l → ł).
_OPERATOR_NAMES = frozenset({
    # Trig / hyperbolic
    "sin", "cos", "tan", "cot", "sec", "csc",
    "sinh", "cosh", "tanh", "coth",
    "arcsin", "arccos", "arctan", "arccot",
    # Logarithms / exponential
    "log", "ln", "lg", "exp",
    # Limits / extrema
    "lim", "limsup", "liminf",
    "min", "max", "sup", "inf",
    "arg", "argmin", "argmax",
    # Linear algebra / misc
    "det", "dim", "ker", "im", "rank", "tr", "diag",
    "deg", "gcd", "lcm", "mod", "hom",
    "Pr", "sgn", "sign",
    # Projections
    "proj", "span",
    # Custom but common
    "softmax", "relu", "ReLU",
    "sg",  # stop-gradient
})

_DELIMITERS: dict[str, str] = {
    "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋",
    "lceil": "⌈", "rceil": "⌉", "lvert": "|", "rvert": "|",
    "lVert": "‖", "rVert": "‖", "vert": "|", "Vert": "‖",
    "{": "{", "}": "}",
}

# Text-mode commands — render their argument as plain text.
_TEXT_CMDS = frozenset({
    "text", "textrm", "textbf", "textit", "texttt", "textsf",
    "mathrm", "mathbf", "mathit", "mathsf", "mathtt",
    "operatorname", "operatorname*",
})

# Commands to silently skip (consume but produce nothing).
_SKIP_CMDS = frozenset({
    "displaystyle", "textstyle", "scriptstyle", "scriptscriptstyle",
    "phantom", "hphantom", "vphantom",
    "mathstrut", "strut", "rule",
    "label", "tag", "notag", "nonumber",
    "hspace", "vspace", "kern", "mkern", "mskip",
    "color", "textcolor", "colorbox",
})

# Spacing commands.
_SPACING: dict[str, str] = {
    "quad": "  ", "qquad": "    ", ",": " ", ";": " ", ":": " ",
    "!": "", " ": " ", "thinspace": " ", "enspace": " ",
    "medspace": " ", "thickspace": " ", "negthinspace": "",
}


# ---------------------------------------------------------------------------
# Core converter
# ---------------------------------------------------------------------------

def _convert(tex: str) -> str:
    """Convert a LaTeX math fragment to Unicode, best-effort.

    Structural commands are parsed here; everything else is delegated
    to ``unicodeit.replace()``.
    """
    out: list[str] = []
    i = 0
    n = len(tex)

    while i < n:
        ch = tex[i]

        # ── backslash commands ──────────────────────────────────────
        if ch == "\\":
            j = i + 1
            if j >= n:
                break
            # Parse command name.
            if tex[j].isalpha():
                while j < n and tex[j].isalpha():
                    j += 1
                cmd = tex[i + 1 : j]
            else:
                cmd = tex[j]
                j += 1

            # Skip whitespace after command.
            while j < n and tex[j] == " ":
                j += 1

            # ── line break \\  ──
            if cmd == "\\":
                _, j = _opt_arg(tex, j)  # skip optional [length]
                out.append("\n")

            # ── environments ──
            elif cmd == "begin":
                env_name, j = _brace_arg(tex, j)
                end_tag = "\\end{" + env_name + "}"
                end_pos = tex.find(end_tag, j)
                if end_pos == -1:
                    env_body, j = tex[j:], n
                else:
                    env_body = tex[j:end_pos]
                    j = end_pos + len(end_tag)
                out.append(_render_environment(env_name, env_body))
            elif cmd == "end":
                # Orphan \end — skip it.
                _, j = _brace_arg(tex, j)

            # ── fractions ──
            elif cmd in ("frac", "dfrac", "tfrac", "cfrac"):
                num, j = _brace_arg(tex, j)
                den, j = _brace_arg(tex, j)
                sn, sd = _convert(num), _convert(den)
                if len(sn) == 1 and len(sd) == 1:
                    # Try compact Unicode fraction.
                    compact = _ucit(f"^{{{num}}}/_{{" + den + "}")
                    if "\\" not in compact:
                        out.append(compact)
                        i = j
                        continue
                out.append(f"({sn})/({sd})")

            # ── roots ──
            elif cmd == "sqrt":
                opt, j = _opt_arg(tex, j)
                arg, j = _brace_arg(tex, j)
                inner = _convert(arg)
                if opt:
                    out.append(f"{_convert(opt)}√({inner})")
                else:
                    out.append(f"√({inner})")

            # ── arrow commands with annotation ──
            elif cmd in _ARROW_COMMANDS:
                opt, j = _opt_arg(tex, j)          # optional [below]
                arg, j = _brace_arg(tex, j)         # {above}
                arrow = _ARROW_COMMANDS[cmd]
                above = _convert(arg) if arg else ""
                below = _convert(opt) if opt else ""
                if above and below:
                    out.append(f"—{above}/{below}{arrow}")
                elif above:
                    out.append(f"—{above}→" if cmd.endswith("rightarrow") else f"←{above}—" if cmd.endswith("leftarrow") else f"—{above}{arrow}")
                else:
                    out.append(arrow)

            # ── stackrel / overset / underset ──
            elif cmd == "stackrel":
                top, j = _brace_arg(tex, j)
                bot, j = _brace_arg(tex, j)
                st, sb = _convert(top), _convert(bot)
                out.append(f"{sb}[{st}]" if len(st) <= 3 else f"{sb}")
            elif cmd == "overset":
                top, j = _brace_arg(tex, j)
                bot, j = _brace_arg(tex, j)
                st, sb = _convert(top), _convert(bot)
                out.append(f"{sb}[{st}]" if len(st) <= 3 else sb)
            elif cmd == "underset":
                bot, j = _brace_arg(tex, j)
                top, j = _brace_arg(tex, j)
                st, sb = _convert(top), _convert(bot)
                out.append(f"{st}[{sb}]" if len(sb) <= 3 else st)

            # ── underbrace / overbrace — render content, attach label ──
            elif cmd in ("underbrace", "overbrace"):
                arg, j = _brace_arg(tex, j)
                inner = _convert(arg)
                # Peek for _/^ label.
                if j < n and tex[j] in ("_", "^"):
                    _, j2 = _brace_arg(tex, j + 1)
                    label_raw = tex[j + 1 : j2] if tex[j + 1] == "{" else tex[j + 1 : j2]
                    label_raw, j = _brace_arg(tex, j + 1)
                    label = _convert(label_raw)
                    out.append(f"{inner} [{label}]")
                else:
                    out.append(inner)

            # ── delimiter sizing ──
            elif cmd in _SIZING_CMDS:
                if j < n:
                    if tex[j] == "\\":
                        k = j + 1
                        while k < n and tex[k].isalpha():
                            k += 1
                        dcmd = tex[j + 1 : k]
                        out.append(_DELIMITERS.get(dcmd, _ucit("\\" + dcmd)))
                        j = k
                    elif tex[j] == ".":
                        j += 1  # \left. or \right. — invisible delimiter
                    else:
                        out.append(_DELIMITERS.get(tex[j], tex[j]))
                        j += 1

            # ── overline / underline ──
            elif cmd == "overline":
                arg, j = _brace_arg(tex, j)
                inner = _convert(arg)
                out.append("".join(c + "\u0305" for c in inner))
            elif cmd == "underline":
                arg, j = _brace_arg(tex, j)
                out.append(_convert(arg))

            # ── text-mode commands ──
            elif cmd in _TEXT_CMDS:
                arg, j = _brace_arg(tex, j)
                out.append(arg)

            # ── skip commands ──
            elif cmd in _SKIP_CMDS:
                if j < n and tex[j] == "{":
                    _, j = _brace_arg(tex, j)

            # ── spacing ──
            elif cmd in _SPACING:
                out.append(_SPACING[cmd])

            # ── bm / boldsymbol — just convert content ──
            elif cmd in ("bm", "boldsymbol", "pmb"):
                arg, j = _brace_arg(tex, j)
                out.append(_convert(arg))

            # ── operator names (must come before unicodeit fallback) ──
            elif cmd in _OPERATOR_NAMES:
                out.append(cmd)

            # ── everything else → unicodeit ──
            else:
                full_cmd = "\\" + cmd
                if j < n and tex[j] == "{":
                    arg, j = _brace_arg(tex, j)
                    # Try unicodeit on the full command+arg.
                    attempt = _ucit(full_cmd + "{" + arg + "}")
                    if "\\" not in attempt:
                        out.append(attempt)
                    else:
                        # unicodeit didn't fully resolve — convert arg ourselves.
                        sym = _ucit(full_cmd)
                        if "\\" in sym:
                            sym = cmd  # strip backslash, show plain name
                        out.append(sym + "(" + _convert(arg) + ")")
                else:
                    result = _ucit(full_cmd)
                    if "\\" in result:
                        result = cmd  # unknown command — just show name
                    out.append(result)

            i = j
            continue

        # ── superscript ──
        if ch == "^":
            arg, i = _brace_arg(tex, i + 1)
            # Let unicodeit handle the super/subscript mapping.
            inner = _convert(arg)
            attempt = _ucit("^{" + inner + "}")
            if attempt.startswith("^{"):
                # unicodeit couldn't map it — use fallback.
                out.append("^(" + inner + ")")
            else:
                out.append(attempt)
            continue

        # ── subscript ──
        if ch == "_":
            arg, i = _brace_arg(tex, i + 1)
            inner = _convert(arg)
            attempt = _ucit("_{" + inner + "}")
            if attempt.startswith("_{"):
                out.append("_(" + inner + ")")
            else:
                out.append(attempt)
            continue

        # ── ampersand (outside matrix context) ──
        if ch == "&":
            out.append("  ")
            i += 1
            continue

        # ── everything else ──
        out.append(ch)
        i += 1

    return "".join(out)


# Alias used by environment rendering.
_convert_latex_fragment = _convert


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")


def latex_to_unicode(text: str) -> str:
    """Replace LaTeX math spans in *text* with Unicode approximations."""

    def _replace_display(m: re.Match) -> str:
        converted = _convert(m.group(1).strip())
        if "\n" in converted:
            return "\n```\n" + converted + "\n```\n"
        return "\n> " + converted + "\n"

    def _replace_inline(m: re.Match) -> str:
        return _convert(m.group(1))

    text = _DISPLAY_MATH_RE.sub(_replace_display, text)
    text = _INLINE_MATH_RE.sub(_replace_inline, text)
    return text
