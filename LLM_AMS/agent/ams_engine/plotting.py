"""Result plotting for AMS scheduling routines.

- **Single-period** routines (DCOPF / RTED / DCPF / ACOPF / DOPF):
  variables are 1-D ``(n_devices,)``. We render a labeled bar chart.

- **Multi-period** routines (ED / UC / variants with horizon):
  variables are 2-D ``(n_devices, n_periods)``. We render a line plot,
  one line per device, x-axis = time period.
"""

import os
from datetime import datetime
from typing import Dict

import matplotlib
import matplotlib.pyplot as plt
import numpy as np


def _output_path(routine: str, kind: str) -> str:
    out_dir = os.getenv("AMS_OUTPUT_DIR") or "generated"
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(out_dir, f"{routine.lower()}_{kind}_{stamp}.png")


def _bar_plot(arr, labels, ylabel, title, save_path):
    fig, ax = plt.subplots(figsize=(max(6, 0.5 * len(arr) + 2), 4))
    x = [str(l) for l in labels] if labels and len(labels) == len(arr) else [str(i) for i in range(len(arr))]
    bars = ax.bar(x, arr, color="#3a86ff")
    for b, v in zip(bars, arr):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.2f}",
                ha="center", va="bottom" if v >= 0 else "top", fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.axhline(0, color="black", lw=0.6)
    ax.grid(True, axis="y", linestyle=":", alpha=0.5)
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    fig.savefig(save_path, dpi=200)
    plt.close(fig)


def _line_plot(arr2d, labels, ylabel, title, save_path):
    """Multi-period line chart — one line per device, x = period index."""
    n_dev, n_t = arr2d.shape
    # Width grows with the horizon but is capped: 168 slots at 0.4"/slot would be a
    # 70" x 4.5" strip that renders as a sliver in the web card. 16" x 5" keeps a
    # week readable; markers only when there is room for them.
    fig, ax = plt.subplots(figsize=(min(16.0, max(7.0, 0.4 * n_t + 3)), 5.0))
    x = np.arange(n_t)
    marker = "o" if n_t <= 48 else None
    for i in range(n_dev):
        lbl = str(labels[i]) if labels and i < len(labels) else str(i)
        ax.plot(x, arr2d[i], marker=marker, markersize=3, linewidth=1.4, label=lbl)
    if n_t > 48:                                       # day boundaries on hourly horizons
        for d in range(24, n_t, 24):
            ax.axvline(d, color="grey", lw=0.5, alpha=0.5)
    ax.set_xlabel("Time period")
    ax.set_ylabel(ylabel)
    ax.set_title(f"{title}  ({n_t} periods)")
    ax.axhline(0, color="black", lw=0.6)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="best", fontsize=8, ncol=max(1, n_dev // 6))
    plt.tight_layout()
    fig.savefig(save_path, dpi=200)
    plt.close(fig)


def plot_results(results: Dict) -> Dict[str, str]:
    """Render result plots. Returns ``{var_name: png_path}``.

    Dispatches to a bar plot for 1-D arrays or a line plot for 2-D arrays.
    Each plot is wrapped in its own try/except so one bad variable does not
    blow up the whole turn.
    """
    routine = results.get("routine", "routine")
    paths: Dict[str, str] = {}

    plots = [
        ("pg",  "Generator output P_g (pu)", results.get("gen_idx", [])),
        ("plf", "Line flow P_lf (pu)",       results.get("line_idx", [])),
        ("pd",  "Load P_d (pu)",             results.get("load_idx", [])),
    ]

    for key, ylabel, labels in plots:
        raw = results.get(key)
        if raw is None:
            continue
        try:
            arr = np.asarray(raw, dtype=float)
        except (TypeError, ValueError):
            continue
        if arr.size == 0:
            continue

        save_path = _output_path(routine, key)
        title = f"{routine} — {key}"
        try:
            if arr.ndim == 1:
                _bar_plot(arr, labels, ylabel, title, save_path)
            elif arr.ndim == 2:
                _line_plot(arr, labels, ylabel, title, save_path)
            else:
                # higher dims unsupported — skip silently
                continue
        except Exception:
            # never let one plot kill the rest
            plt.close("all")
            continue
        paths[key] = save_path

    return paths
