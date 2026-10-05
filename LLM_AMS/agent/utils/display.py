"""Rich-based terminal UI for LLM-AMS.

Visual conventions (set in ``agent.utils.theme``):
- [?]  blue   — choice required from user
- [i]  grey   — passive info / hint
- [✓]  green  — success / state change
- [✗]  red    — failure
- ❯    green  — input prompt

``info() / ok() / fail()`` accept inline rich markup (e.g. ``[value]foo[/]``)
so callers can highlight values inside the message.
"""

from __future__ import annotations

import os
import sys
from typing import List, Sequence

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from agent.utils.theme import (
    BRAND_GREEN,
    SYM_CHOICE,
    SYM_FAIL,
    SYM_INFO,
    SYM_OK,
    SYM_PROMPT,
    THEME,
)

console = Console(theme=THEME, file=sys.stderr)


# --------------------------------------------------------------- Banner
# ANSI Shadow font (pyfiglet) — pre-rendered to avoid runtime dependency.
_BANNER_LINES = [
    "██╗     ██╗     ███╗   ███╗               ██████╗██╗   ██╗██████╗ ███████╗███╗   ██╗████████╗",
    "██║     ██║     ████╗ ████║              ██╔════╝██║   ██║██╔══██╗██╔════╝████╗  ██║╚══██╔══╝",
    "██║     ██║     ██╔████╔██║    █████╗    ██║     ██║   ██║██████╔╝█████╗  ██╔██╗ ██║   ██║   ",
    "██║     ██║     ██║╚██╔╝██║    ╚════╝    ██║     ██║   ██║██╔══██╗██╔══╝  ██║╚██╗██║   ██║   ",
    "███████╗███████╗██║ ╚═╝ ██║              ╚██████╗╚██████╔╝██║  ██║███████╗██║ ╚████║   ██║   ",
    "╚══════╝╚══════╝╚═╝     ╚═╝               ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ",
    "",
    "        ██╗  ████████╗██████╗                █████╗ ███╗   ███╗███████╗                      ",
    "        ██║  ╚══██╔══╝██╔══██╗              ██╔══██╗████╗ ████║██╔════╝                      ",
    "        ██║     ██║   ██████╔╝    █████╗    ███████║██╔████╔██║███████╗                      ",
    "        ██║     ██║   ██╔══██╗    ╚════╝    ██╔══██║██║╚██╔╝██║╚════██║                      ",
    "        ███████╗██║   ██████╔╝              ██║  ██║██║ ╚═╝ ██║███████║                      ",
    "        ╚══════╝╚═╝   ╚═════╝               ╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝                      ",
]


def display_banner():
    """First-turn-only ASCII banner: LLM-CURENT / LTB-AMS in brand green."""
    for ln in _BANNER_LINES:
        console.print(ln, style="brand")
    console.print()
    sub = Text()
    sub.append("Conversational scheduling agent for ", style="muted")
    sub.append("CURENT LTB AMS", style="brand")
    sub.append("   ·   Routes 1-7   ·   v0.2\n", style="muted")
    sub.append("Type ", style="muted")
    sub.append("?", style="kbd")
    sub.append(" for help / examples · ", style="muted")
    sub.append("quit", style="kbd")
    sub.append(" to exit", style="muted")
    console.print(sub)
    console.print()


# --------------------------------------------------------------- Routine table
ROUTINE_TYPES = [
    ("ACED", "ACOPF, ACOPF1, OPF"),
    ("DCED", "DCOPF, DCOPF2, RTED, RTEDDG, RTEDESP, RTEDES, RTEDVIS, "
             "RTED2, RTED2DG, RTED2ESP, RTED2ES, ED, EDDG, EDES, "
             "ED2, ED2DG, ED2ES, DCOPF1"),
    ("DCUC", "UC, UCDG, UCES, UC2, UC2DG, UC2ES"),
    ("DED",  "DOPF, DOPFVIS"),
    ("PF",   "DCPF, PFlow, DCPF1, PFlow1"),
]
ROUTINE_DOCS_URL = "https://ltb.readthedocs.io/projects/ams/en/latest/routineref.html"


def display_routines_table():
    table = Table(
        title="Available routines",
        title_style="brand",
        show_lines=False,
        header_style="brand",
        border_style="brand_dim",
        padding=(0, 1),
    )
    table.add_column("Type", style="value", no_wrap=True)
    table.add_column("Routines", style="white")
    for t, rs in ROUTINE_TYPES:
        table.add_row(t, rs)
    console.print(table)
    console.print(
        Text.assemble(
            ("Details at ", "muted"),
            (ROUTINE_DOCS_URL, "brand"),
        )
    )
    console.print()


# --------------------------------------------------------------- Solver table
def display_solver_table(compat: Sequence[str], default: str):
    DESCS = {
        "CLARABEL": "open-source · LP / QP / SOCP   (cvxpy default)",
        "OSQP":     "open-source · QP",
        "SCS":      "open-source · LP / QP / SOCP",
        "HIGHS":    "open-source · LP / MILP",
        "SCIPY":    "open-source · LP / MILP via HiGHS (in scipy)",
        "SCIP":     "open-source · MILP / MINLP",
        "ECOS":     "open-source · LP / SOCP",
        "GUROBI":   "commercial  · LP / QP / MIP / MISOCP",
        "MOSEK":    "commercial  · LP / QP / SOCP / MIP / MISOCP",
        "CPLEX":    "commercial  · LP / QP / MIP",
        "COPT":     "commercial  · LP / QP / MIP",
        "(internal: pypower)": "PYPOWER-internal solver (no cvxpy choice)",
    }
    table = Table(show_header=True, header_style="muted",
                  border_style="brand_dim", padding=(0, 1))
    table.add_column("", no_wrap=True)
    table.add_column("Solver", style="value", no_wrap=True)
    table.add_column("Notes", style="muted")
    for s in compat:
        marker = Text("●", style="brand") if s == default else Text("○", style="muted")
        notes = DESCS.get(s, "")
        if s == default:
            notes = f"{notes}  [italic]default[/]" if notes else "default"
        table.add_row(marker, s, Text.from_markup(notes))
    console.print(table)
    console.print()


# --------------------------------------------------------------- Choice prompt
def _prefix(sym: str, style: str) -> Text:
    """Return a Text containing the literal symbol — no rich-markup parsing."""
    return Text(sym + " ", style=style)


def choice_prompt(question: str, default: str | None = None) -> str:
    """Distinct visual style for a question that *requires* a choice."""
    console.print(_prefix(SYM_CHOICE, "choice") + Text(question, style="value"))
    # The input prompt itself is markup-parsed; SYM_PROMPT (❯) is bracket-free.
    suffix = ""
    if default is not None:
        suffix = f"  [muted](default: [/][default]{default}[/][muted])[/]"
    return console.input(f"  [prompt]{SYM_PROMPT}[/] {suffix}  ").strip()


# --------------------------------------------------------------- Info / status
def info(msg: str):
    """Passive info. ``msg`` may contain rich markup."""
    console.print(_prefix(SYM_INFO, "muted") + Text.from_markup(msg))


def ok(msg: str):
    """Success state change. ``msg`` may contain rich markup."""
    console.print(_prefix(SYM_OK, "ok") + Text.from_markup(msg))


def fail(msg: str):
    """Error. ``msg`` may contain rich markup."""
    console.print(_prefix(SYM_FAIL, "fail") + Text.from_markup(msg))


def shorten_path(path: str, width: int = 50) -> str:
    if len(path) <= width:
        return path
    parts = path.split(os.sep)
    keep = []
    used = 0
    for p in reversed(parts):
        if used + len(p) + 1 > width - 3:
            break
        keep.append(p)
        used += len(p) + 1
    return "…/" + "/".join(reversed(keep))


def display_status_bar(inputs):
    """One-line sticky bar above the prompt."""
    n_mods = (
        len(inputs.load_overrides)
        + len(inputs.gen_off)
        + len(inputs.line_off)
        + len(inputs.line_rate_overrides)
        + len(inputs.disabled_constraints)
        + int(bool(inputs.formulation_edit))
    )
    case_short = shorten_path(inputs.case_path, width=42)
    line = Text()
    line.append("┌─ ", style="brand_dim")
    line.append("case ",    style="label"); line.append(case_short, style="value"); line.append("  ·  ", style="muted")
    line.append("routine ", style="label"); line.append(inputs.routine, style="value"); line.append("  ·  ", style="muted")
    line.append("solver ",  style="label"); line.append(inputs.solver, style="value"); line.append("  ·  ", style="muted")
    line.append("mods ",    style="label")
    line.append(str(n_mods), style="value" if n_mods else "muted")
    line.append(" ─┐", style="brand_dim")
    console.print(line)


def display_initial_case_summary(info_dict: dict):
    msg = (
        f"Loaded [value]{shorten_path(info_dict['case_path'], 60)}[/]  "
        f"— [value]{info_dict['n_bus']}[/] buses, [value]{info_dict['n_line']}[/] lines, "
        f"[value]{info_dict['n_pq']}[/] loads, [value]{info_dict['n_staticgen']}[/] generators"
    )
    ok(msg)
    console.print()


# --------------------------------------------------------------- Help
def display_help():
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="value", no_wrap=True)
    table.add_column(style="muted")
    rows = [
        ("What can I ask?", ""),
        ("",  "Concept Q&A — 'What is RTED?'  'Why disable plflb?'"),
        ("",  "Discovery   — 'Which solvers work with UC?'  'List loads'"),
        ("",  "Case I/O    — 'Load case ieee14_uced'  'Show case info'"),
        ("",  "Configure   — 'Use solver SCS'  'Set interval to 1 hour'"),
        ("",  "                'Disable plflb and plfub'"),
        ("",  "Modify      — 'Change load PQ_1 to 3.2'  'Trip PV_1'"),
        ("",  "                'Trip line Line_2'  'Set Line_3 rate to 0.6'"),
        ("",  "Solve       — 'Solve'  'Run the dispatch'"),
        ("",  "Compound    — 'Trip PV_1 then solve'  'Change PQ_1 to 3 and solve'"),
        ("", ""),
        ("Special inputs", ""),
        ("?",     "show this help"),
        ("quit",  "exit"),
    ]
    for a, b in rows:
        table.add_row(a, b)
    console.print(table)
    console.print()


# --------------------------------------------------------------- Streaming output
NODE_DISPLAY = {
    "classifier":         "classifying message",
    "router":             "routing",
    "planner":            "planning multi-step",
    "step_controller":    "starting next step",
    "advance_step":       "advancing",
    "summary":            "summarizing",
    "question_general":   "answering",
    "question_parameter": "listing resources",
    "case_io":            "loading / inspecting case",
    "configure":          "updating configuration",
    "modify":             "modifying system",
    "solve":              "solving routine",
    "error_handler":      "handling error",
}


def display_executing_node(node_name: str):
    """Per-node progress hint (dim, single line)."""
    label = NODE_DISPLAY.get(node_name, node_name)
    console.print(f"  [muted]→ {label}…[/]")


def display_response(content: str):
    """Agent's final reply as rendered Markdown."""
    console.print()
    console.print(Markdown(content))
    console.print()


def input_prompt(hint: bool = False) -> str:
    """The main turn-input prompt.

    Style: ``❯ Message ▸`` — chevron + label + arrow, all bold green.
    Pass ``hint=True`` to print the one-line gray hint above (used only on
    the first turn after startup; ``?`` retrieves it later).
    """
    if hint:
        console.print("[muted]   Type a question, command, or '?' for help.[/]")
    return console.input(
        f"  [prompt]{SYM_PROMPT} Message ▸[/]  "
    ).strip()


# --------------------------------------------------------------- Routine snapshot + math
def display_snapshot(snapshot):
    """Render the list returned by ``ams_engine.snapshots.get_snapshot``."""
    if not snapshot:
        return
    label = snapshot[0][0]
    console.print(f"[brand]┌─ {label}[/]")
    for _, tbl in snapshot:
        console.print(tbl)
    console.print()


def display_formulation(text: str):
    """Render a Unicode-art math block.

    Three styling tiers for readability:
    - box-drawing frame (╔ ║ ╚)            → brand green
    - section labels  (Decision variables:, Objective:, Constraints:)
                                            → brand green, bold
    - math / equation body                  → dark grey (#3a3a3a) so it stays
                                              readable on light terminal themes
    """
    if not text:
        return
    section_labels = ("Decision variables:", "Objective:", "Constraints:",
                      "Solve for", "Solve (find)")
    for line in text.rstrip().split("\n"):
        stripped = line.lstrip()
        if line.startswith(("╔", "║", "╚")):
            console.print(line, style="brand")
        elif any(stripped.startswith(lbl) for lbl in section_labels):
            console.print(line, style="brand")
        else:
            console.print(line, style="grey23")
    console.print()


# --------------------------------------------------------------- Constraint check
_SEV_STYLE = {
    "OK":        ("ok",    "✓"),
    "LOW":       ("muted", "•"),
    "WARN":      ("warn",  "!"),
    "VIOLATION": ("fail",  "✗"),
}


def display_constraint_check(items):
    """Render the list returned by ``constraint_check.check_constraints``."""
    if not items:
        return
    console.print("[brand]┌─ Constraint check[/]")
    rule = "[brand_dim]" + "─" * 76 + "[/]"
    console.print(rule)
    for item, value, sev in items:
        style, glyph = _SEV_STYLE.get(sev, ("white", "·"))
        line = Text()
        line.append(f"  {glyph} ", style=style)
        line.append(f"{item:<32}", style="value")
        line.append(f"  {value:<28}", style="white")
        line.append(sev, style=style)
        console.print(line)
    console.print(rule)
    console.print()
