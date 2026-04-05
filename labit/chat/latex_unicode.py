"""Convert LaTeX math notation to Unicode approximations for terminal display."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Symbol tables
# ---------------------------------------------------------------------------

_GREEK: dict[str, str] = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "ϕ", "chi": "χ", "psi": "ψ", "omega": "ω",
    # Uppercase
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω",
}

_OPERATORS: dict[str, str] = {
    "sum": "∑", "prod": "∏", "int": "∫", "iint": "∬", "iiint": "∭",
    "oint": "∮", "partial": "∂", "nabla": "∇", "infty": "∞",
    "forall": "∀", "exists": "∃", "nexists": "∄", "emptyset": "∅",
    "varnothing": "∅",
}

_RELATIONS: dict[str, str] = {
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "supset": "⊃",
    "subseteq": "⊆", "supseteq": "⊇", "leq": "≤", "le": "≤",
    "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈",
    "sim": "∼", "simeq": "≃", "cong": "≅", "equiv": "≡",
    "propto": "∝", "ll": "≪", "gg": "≫", "prec": "≺", "succ": "≻",
    "preceq": "⪯", "succeq": "⪰",
}

_ARROWS: dict[str, str] = {
    "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔",
    "mapsto": "↦", "uparrow": "↑", "downarrow": "↓",
    "longrightarrow": "⟶", "longleftarrow": "⟵",
}

_MISC: dict[str, str] = {
    "cdot": "·", "cdots": "⋯", "ldots": "…", "dots": "…", "vdots": "⋮",
    "ddots": "⋱", "times": "×", "div": "÷", "circ": "∘", "bullet": "•",
    "star": "⋆", "dagger": "†", "pm": "±", "mp": "∓",
    "mid": "|", "nmid": "∤", "parallel": "∥",
    "perp": "⊥", "angle": "∠", "triangle": "△",
    "neg": "¬", "land": "∧", "lor": "∨", "wedge": "∧", "vee": "∨",
    "cap": "∩", "cup": "∪", "setminus": "∖",
    "ell": "ℓ", "hbar": "ℏ", "Re": "ℜ", "Im": "ℑ", "wp": "℘",
    "aleph": "ℵ",
}

_SPACING: dict[str, str] = {
    "quad": "  ", "qquad": "    ", ",": " ", ";": " ", "!": "", " ": " ",
}

# Merge all simple command tables.
_SIMPLE_COMMANDS: dict[str, str] = {}
for _tbl in (_GREEK, _OPERATORS, _RELATIONS, _ARROWS, _MISC, _SPACING):
    _SIMPLE_COMMANDS.update(_tbl)

# Blackboard bold  \mathbb{X}
_MATHBB: dict[str, str] = {
    "A": "𝔸", "B": "𝔹", "C": "ℂ", "D": "𝔻", "E": "𝔼", "F": "𝔽",
    "G": "𝔾", "H": "ℍ", "I": "𝕀", "J": "𝕁", "K": "𝕂", "L": "𝕃",
    "M": "𝕄", "N": "ℕ", "O": "𝕆", "P": "ℙ", "Q": "ℚ", "R": "ℝ",
    "S": "𝕊", "T": "𝕋", "U": "𝕌", "V": "𝕍", "W": "𝕎", "X": "𝕏",
    "Y": "𝕐", "Z": "ℤ",
}

# Calligraphic  \mathcal{X}
_MATHCAL: dict[str, str] = {
    "A": "𝒜", "B": "ℬ", "C": "𝒞", "D": "𝒟", "E": "ℰ", "F": "ℱ",
    "G": "𝒢", "H": "ℋ", "I": "ℐ", "J": "𝒥", "K": "𝒦", "L": "ℒ",
    "M": "ℳ", "N": "𝒩", "O": "𝒪", "P": "𝒫", "Q": "𝒬", "R": "ℛ",
    "S": "𝒮", "T": "𝒯", "U": "𝒰", "V": "𝒱", "W": "𝒲", "X": "𝒳",
    "Y": "𝒴", "Z": "𝒵",
}

# Unicode superscripts / subscripts (limited charset).
_SUPERSCRIPTS: dict[str, str] = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
    "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "i": "ⁱ", "n": "ⁿ", "t": "ᵗ", "T": "ᵀ",
    "*": "∗",
}

_SUBSCRIPTS: dict[str, str] = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅",
    "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
    "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}

# Combining diacritics for \hat, \bar, \tilde, etc.
_ACCENTS: dict[str, str] = {
    "hat": "\u0302",    # combining circumflex
    "bar": "\u0304",    # combining macron
    "tilde": "\u0303",  # combining tilde
    "dot": "\u0307",    # combining dot above
    "ddot": "\u0308",   # combining diaeresis
    "vec": "\u20d7",    # combining right arrow above
    "check": "\u030c",  # combining caron
}

# Big delimiters — just strip the \left / \right prefix.
_DELIMITERS: dict[str, str] = {
    "langle": "⟨", "rangle": "⟩",
    "lfloor": "⌊", "rfloor": "⌋",
    "lceil": "⌈", "rceil": "⌉",
    "lvert": "|", "rvert": "|",
    "lVert": "‖", "rVert": "‖",
    "{": "{", "}": "}",
}

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

def _brace_arg(tex: str, pos: int) -> tuple[str, int]:
    r"""Extract a brace-delimited argument ``{...}`` starting at *pos*.

    Returns ``(inner_text, end_pos)`` where *end_pos* is just past the
    closing ``}``.  If *pos* doesn't point at ``{``, return the single
    next token as the argument — either a ``\command`` or a single char.
    """
    if pos >= len(tex):
        return ("", pos)
    if tex[pos] != "{":
        # Handle \command as a single token (e.g. ^*  or  ^\infty  or  _\theta)
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
    # Unmatched brace — return rest of string.
    return (tex[start:], len(tex))


def _try_superscript(text: str) -> str:
    """Convert *text* to Unicode superscript if all chars are mappable."""
    converted = [_SUPERSCRIPTS.get(ch) for ch in text]
    if all(c is not None for c in converted):
        return "".join(converted)  # type: ignore[arg-type]
    return "^(" + text + ")"


def _try_subscript(text: str) -> str:
    converted = [_SUBSCRIPTS.get(ch) for ch in text]
    if all(c is not None for c in converted):
        return "".join(converted)  # type: ignore[arg-type]
    return "_(" + text + ")"


# ---------------------------------------------------------------------------
# Core converter
# ---------------------------------------------------------------------------

def _convert_latex_fragment(tex: str) -> str:
    """Convert a single LaTeX math fragment to Unicode, best-effort."""
    out: list[str] = []
    i = 0
    n = len(tex)
    while i < n:
        ch = tex[i]

        # --- backslash commands ---
        if ch == "\\":
            # Grab command name (letters only, or single non-letter).
            j = i + 1
            if j < n and tex[j].isalpha():
                while j < n and tex[j].isalpha():
                    j += 1
                cmd = tex[i + 1 : j]
            elif j < n:
                cmd = tex[j]
                j += 1
            else:
                i = j
                continue

            # Skip optional whitespace after command name.
            while j < n and tex[j] == " ":
                j += 1

            # -- font commands: \mathbb, \mathcal, \mathrm, \mathbf, \text, \operatorname --
            if cmd == "mathbb":
                arg, j = _brace_arg(tex, j)
                out.append("".join(_MATHBB.get(c, c) for c in arg))
            elif cmd == "mathcal":
                arg, j = _brace_arg(tex, j)
                out.append("".join(_MATHCAL.get(c, c) for c in arg))
            elif cmd in ("mathrm", "text", "textrm", "operatorname", "mathbf", "textbf", "mathit"):
                arg, j = _brace_arg(tex, j)
                out.append(arg)
            elif cmd in ("boldsymbol", "bm"):
                arg, j = _brace_arg(tex, j)
                out.append(_convert_latex_fragment(arg))

            # -- accents: \hat{x}, \bar{x}, etc. --
            elif cmd in _ACCENTS:
                arg, j = _brace_arg(tex, j)
                inner = _convert_latex_fragment(arg)
                # Apply combining character to first char.
                if inner:
                    out.append(inner[0] + _ACCENTS[cmd] + inner[1:])
                else:
                    out.append(_ACCENTS[cmd])

            # -- fractions --
            elif cmd == "frac":
                num, j = _brace_arg(tex, j)
                den, j = _brace_arg(tex, j)
                num_u = _convert_latex_fragment(num)
                den_u = _convert_latex_fragment(den)
                # Try Unicode super/sub for simple single-char fracs.
                if len(num_u) == 1 and len(den_u) == 1:
                    sup = _SUPERSCRIPTS.get(num_u)
                    sub = _SUBSCRIPTS.get(den_u)
                    if sup and sub:
                        out.append(f"{sup}⁄{sub}")
                        i = j
                        continue
                out.append(f"({num_u})/({den_u})")

            # -- sqrt --
            elif cmd == "sqrt":
                arg, j = _brace_arg(tex, j)
                out.append("√(" + _convert_latex_fragment(arg) + ")")

            # -- delimiters --
            elif cmd in ("left", "right", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr", "Biggl", "Biggr", "big", "Big"):
                # Next char is the delimiter.
                if j < n:
                    delim = tex[j]
                    if delim == "\\":
                        # e.g. \left\langle
                        k = j + 1
                        while k < n and tex[k].isalpha():
                            k += 1
                        delim_cmd = tex[j + 1 : k]
                        out.append(_DELIMITERS.get(delim_cmd, delim_cmd))
                        j = k
                    else:
                        out.append(_DELIMITERS.get(delim, delim))
                        j += 1

            # -- overline / underline --
            elif cmd == "overline":
                arg, j = _brace_arg(tex, j)
                inner = _convert_latex_fragment(arg)
                out.append("".join(c + "\u0305" for c in inner))
            elif cmd == "underline":
                arg, j = _brace_arg(tex, j)
                out.append(_convert_latex_fragment(arg))

            # -- \underbrace / \overbrace --
            elif cmd in ("underbrace", "overbrace"):
                arg, j = _brace_arg(tex, j)
                out.append(_convert_latex_fragment(arg))

            # -- spacing: \, \; \! \  \quad \qquad --
            elif cmd in _SPACING:
                out.append(_SPACING[cmd])

            # -- stop-gradient and common operator names --
            elif cmd == "sg":
                arg, j = _brace_arg(tex, j)
                out.append("sg(" + _convert_latex_fragment(arg) + ")")
            elif cmd == "max":
                out.append("max")
            elif cmd == "min":
                out.append("min")
            elif cmd == "arg":
                out.append("arg")
            elif cmd in ("log", "ln", "exp", "sin", "cos", "tan", "det", "dim", "sup", "inf", "lim", "argmax", "argmin"):
                out.append(cmd)

            # -- simple symbol lookup --
            elif cmd in _SIMPLE_COMMANDS:
                out.append(_SIMPLE_COMMANDS[cmd])

            # -- delimiter names --
            elif cmd in _DELIMITERS:
                out.append(_DELIMITERS[cmd])

            # -- unknown command: keep name as-is --
            else:
                # Check if it has a brace argument — consume it.
                if j < n and tex[j] == "{":
                    arg, j = _brace_arg(tex, j)
                    out.append(cmd + "(" + _convert_latex_fragment(arg) + ")")
                else:
                    out.append(cmd)

            i = j
            continue

        # --- superscript ---
        if ch == "^":
            arg, i = _brace_arg(tex, i + 1)
            inner = _convert_latex_fragment(arg)
            out.append(_try_superscript(inner))
            continue

        # --- subscript ---
        if ch == "_":
            arg, i = _brace_arg(tex, i + 1)
            inner = _convert_latex_fragment(arg)
            out.append(_try_subscript(inner))
            continue

        # --- everything else: pass through ---
        out.append(ch)
        i += 1

    return "".join(out)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Match display math ($$...$$) and inline math ($...$).
# Use non-greedy matching; handle multi-line display math.
_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")


def latex_to_unicode(text: str) -> str:
    """Replace LaTeX math spans in *text* with Unicode approximations.

    Display math (``$$...$$``) is converted and placed on its own indented
    line.  Inline math (``$...$``) is converted in-place.
    """

    def _replace_display(m: re.Match) -> str:
        converted = _convert_latex_fragment(m.group(1).strip())
        return "\n    " + converted + "\n"

    def _replace_inline(m: re.Match) -> str:
        return _convert_latex_fragment(m.group(1))

    text = _DISPLAY_MATH_RE.sub(_replace_display, text)
    text = _INLINE_MATH_RE.sub(_replace_inline, text)
    return text
