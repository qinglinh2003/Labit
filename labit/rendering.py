"""Extended Rich Markdown with LaTeX math support.

Uses ``mdit-py-plugins`` dollarmath to properly parse ``$...$`` and
``$$...$$`` blocks, then converts them to Unicode via ``unicodeit``
for terminal display.
"""

from __future__ import annotations

import re
from typing import Iterable

import unicodeit
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown, MarkdownElement
from rich.text import Text

# ---------------------------------------------------------------------------
# LaTeX → Unicode pre/post-processing
# ---------------------------------------------------------------------------

# Operators that unicodeit mis-converts via greedy prefix matching
# (e.g. \l→ł eats \log, \lim; \in→∈ eats \inf)
_OPERATOR_RE = re.compile(
    r"\\(?:log|ln|lim|limsup|liminf|inf|sin|cos|tan|sec|csc|cot|"
    r"arcsin|arccos|arctan|sinh|cosh|tanh|exp|det|dim|ker|deg|"
    r"hom|arg|max|min|sup|gcd|Pr)\b"
)

# Structural commands handled before unicodeit.
# Use a helper for nested-brace matching since {content} may contain {sub}.
def _brace_arg(s: str, pos: int) -> tuple[str, int] | None:
    """Extract ``{...}`` at *pos*, handling one level of nesting. Returns (content, end)."""
    if pos >= len(s) or s[pos] != "{":
        return None
    depth, start = 1, pos + 1
    i = start
    while i < len(s) and depth > 0:
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return s[start : i - 1], i

_TEXT_CMD_RE = re.compile(r"\\(?:text|mathrm|textbf|textit|operatorname)\{([^}]*)\}")
_SQRT_RE = re.compile(r"\\sqrt\{([^}]*)\}")

# Commands stripped entirely (no terminal representation)
_SIZING_RE = re.compile(r"\\(?:big|Big|bigg|Bigg|left|right|middle)(?![a-zA-Z])")
_ENV_DELIM_RE = re.compile(r"\\(?:begin|end)\{[^}]*\}")
_PHANTOM_RE = re.compile(r"\\(?:phantom|hphantom|vphantom|mathrlap|mathllap|mathclap)\{[^}]*\}")
_STYLE_RE = re.compile(r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle)\b")

# Spacing / alignment commands → whitespace
_SPACING_MAP: dict[str, str] = {
    r"\qquad": "    ",
    r"\quad": "  ",
    r"\,": " ",
    r"\;": " ",
    r"\:": " ",
    r"\!": "",
    r"\enspace": " ",
    r"\hfill": "  ",
    r"\\": "\n",
    "&": "  ",
}

# Unicode fixups applied after unicodeit (wrong codepoints)
_UNICODE_FIXUPS: dict[str, str] = {
    "\u3008": "\u27E8",  # 〈 (CJK) → ⟨ (math)
    "\u3009": "\u27E9",  # 〉 (CJK) → ⟩ (math)
}


def _expand_braced_commands(s: str) -> str:
    """Handle commands with brace arguments that may be nested, e.g. \\frac{a+1}{b}."""
    _COMMANDS: dict[str, int] = {
        # command prefix → number of brace args
        "\\frac": 2,
        "\\dfrac": 2,
        "\\tfrac": 2,
        "\\xrightarrow": 1,
        "\\xleftarrow": 1,
        "\\xleftrightarrow": 1,
        "\\overset": 2,
        "\\underset": 2,
        "\\stackrel": 2,
    }
    _ARROW_CMDS = {"\\xrightarrow", "\\xleftarrow", "\\xleftrightarrow"}
    _ARROW_CHAR = {"\\xrightarrow": "→", "\\xleftarrow": "←", "\\xleftrightarrow": "↔"}

    for cmd, nargs in _COMMANDS.items():
        while cmd in s:
            idx = s.find(cmd)
            if idx == -1:
                break
            pos = idx + len(cmd)
            args: list[str] = []
            ok = True
            for _ in range(nargs):
                # skip optional whitespace
                while pos < len(s) and s[pos] == " ":
                    pos += 1
                result = _brace_arg(s, pos)
                if result is None:
                    ok = False
                    break
                content, pos = result
                args.append(_latex_to_unicode(content))  # recursive!
            if not ok:
                break
            # Build replacement
            if cmd in _ARROW_CMDS:
                arrow = _ARROW_CHAR[cmd]
                repl = f" {arrow}({args[0]}) " if args[0].strip() else f" {arrow} "
            elif cmd in ("\\frac", "\\dfrac", "\\tfrac"):
                repl = f"{args[0]}/{args[1]}"
            elif cmd in ("\\overset", "\\stackrel"):
                repl = f"{args[1]}^{args[0]}"
            elif cmd == "\\underset":
                repl = f"{args[1]}_{args[0]}"
            else:
                repl = "".join(args)
            s = s[:idx] + repl + s[pos:]
    return s


def _latex_to_unicode(latex: str) -> str:
    """Best-effort LaTeX → Unicode conversion for terminal display."""
    s = latex

    # --- structural transforms (order matters) ---
    s = _TEXT_CMD_RE.sub(r"\1", s)
    s = _expand_braced_commands(s)
    s = _SQRT_RE.sub(r"√(\1)", s)
    s = _SIZING_RE.sub("", s)
    s = _ENV_DELIM_RE.sub("", s)
    s = _PHANTOM_RE.sub("", s)
    s = _STYLE_RE.sub("", s)

    # --- spacing / alignment ---
    for cmd, repl in _SPACING_MAP.items():
        s = s.replace(cmd, repl)

    # --- protect operators from unicodeit's greedy matching ---
    protected: list[str] = []

    def _protect(m: re.Match) -> str:
        idx = len(protected)
        protected.append(m.group(0)[1:])  # strip leading backslash
        return f"\x00P{idx}\x00"

    s = _OPERATOR_RE.sub(_protect, s)

    # --- main symbol conversion ---
    s = unicodeit.replace(s)

    # --- restore protected operators ---
    for idx, name in enumerate(protected):
        s = s.replace(f"\x00P{idx}\x00", name)

    # --- unicode fixups ---
    for wrong, right in _UNICODE_FIXUPS.items():
        s = s.replace(wrong, right)

    # --- clean up residual artifacts ---
    # Strip leftover backslash commands that unicodeit didn't handle
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    # Clean up residual sub/superscript braces:
    #   single-char: _{x} → _x,  ^{x} → ^x
    #   multi-char:  _{ab} → _(ab),  ^{ab} → ^(ab)
    def _strip_braces(m: re.Match) -> str:
        prefix, content = m.group(1), m.group(2)
        if len(content) <= 1:
            return f"{prefix}{content}"
        return f"{prefix}({content})"
    s = re.sub(r"([_^])\{([^}]+)\}", _strip_braces, s)
    # Strip empty braces
    s = s.replace("{}", "")
    # Ensure space between consecutive operator names (e.g. loglog → log log)
    s = re.sub(r"([a-z])(log|ln|sin|cos|tan|exp|det|dim|lim|min|max|sup|inf|gcd|arg)\b", r"\1 \2", s)
    # Collapse multiple spaces (but preserve intentional newlines)
    s = re.sub(r"[^\S\n]{3,}", "  ", s)

    return s.strip()


# ---------------------------------------------------------------------------
# Rich Markdown elements & subclass
# ---------------------------------------------------------------------------


class MathBlock(MarkdownElement):
    """Renders a display-math block (``$$...$$``)."""

    new_line = True

    @classmethod
    def create(cls, markdown: Markdown, token: Token) -> MathBlock:
        element = cls()
        element.latex = token.content.strip()
        return element

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        yield Text(
            _latex_to_unicode(self.latex), style="italic", justify="center"
        )
        yield Text()


class LaTeXMarkdown(Markdown):
    """Rich Markdown subclass that understands LaTeX math delimiters."""

    elements = {**Markdown.elements, "math_block": MathBlock}

    def __init__(self, markup: str, **kwargs) -> None:  # noqa: D107
        parser = MarkdownIt().enable("strikethrough").enable("table")
        dollarmath_plugin(parser, double_inline=True)

        self.markup = markup
        self.parsed = parser.parse(markup)
        self.code_theme = kwargs.get("code_theme", "monokai")
        self.justify = kwargs.get("justify")
        self.style = kwargs.get("style", "none")
        self.hyperlinks = kwargs.get("hyperlinks", True)
        self.inline_code_lexer = kwargs.get("inline_code_lexer")
        self.inline_code_theme = kwargs.get("inline_code_theme") or self.code_theme

    # ------------------------------------------------------------------
    # Override _flatten_tokens to convert math_inline → plain text with
    # Unicode symbols, so the parent __rich_console__ renders it inline.
    # ------------------------------------------------------------------
    def _flatten_tokens(self, tokens: Iterable[Token]) -> Iterable[Token]:
        for token in super()._flatten_tokens(tokens):
            if token.type == "math_inline":
                yield Token(
                    type="text",
                    tag="",
                    nesting=0,
                    content=_latex_to_unicode(token.content),
                )
            else:
                yield token
