"""Post-solve constraint / violation summary.

After a solve completes (whether or not it converged), check the realized
solution against the case's physical limits and produce a list of items
flagging violations or near-violations.

The check is conservative — it works directly with the solved values stored
on the active routine (``pg``, ``plf``) plus the static limits on the
``StaticGen`` / ``Line`` / ``Bus`` models.
"""

from typing import List, Tuple

import numpy as np

# Severity tags (sortable by violation strength)
SEV_OK    = "OK"
SEV_LOW   = "LOW"
SEV_WARN  = "WARN"
SEV_VIOL  = "VIOLATION"


def _max_over_time(arr) -> np.ndarray:
    """Return per-device max across the time axis, or the array itself if 1-D."""
    a = np.asarray(arr, dtype=float)
    if a.ndim == 1:
        return a
    return np.abs(a).max(axis=1)


def _min_over_time(arr) -> np.ndarray:
    a = np.asarray(arr, dtype=float)
    if a.ndim == 1:
        return a
    return a.min(axis=1)


def _abs_max_over_time(arr) -> np.ndarray:
    a = np.asarray(arr, dtype=float)
    if a.ndim == 1:
        return np.abs(a)
    return np.abs(a).max(axis=1)


def check_constraints(ams_ctx, results: dict) -> List[Tuple[str, str, str]]:
    """Return a list of ``(item, value, severity)`` tuples.

    Empty list means "no checks ran" (e.g. ACOPF where bus voltage isn't
    in ``results``).
    """
    items: List[Tuple[str, str, str]] = []
    ss = ams_ctx.system
    if ss is None:
        return items

    # --- Gen output vs Pmin / Pmax ---
    pg = results.get("pg")
    if pg is not None:
        try:
            gen_idx = list(ss.StaticGen.idx.v)
            pmax = np.asarray(ss.StaticGen.pmax.v, dtype=float)
            pmin = np.asarray(ss.StaticGen.pmin.v, dtype=float)
            online = np.asarray(ss.StaticGen.get(src="u", idx=gen_idx, attr="v"), dtype=float)
            pmax = pmax * online
            pmin = pmin * online
            pg_max = _max_over_time(pg)
            pg_min = _min_over_time(pg)
            for i, idx_str in enumerate(gen_idx):
                if i >= len(pmax):
                    break
                # Upper limit
                if pg_max[i] > pmax[i] * 1.001:
                    items.append((
                        f"Gen {idx_str} above Pmax",
                        f"{pg_max[i]:.4f}  >  {pmax[i]:.4f}  pu",
                        SEV_VIOL,
                    ))
                elif pmax[i] > 0 and pg_max[i] > pmax[i] * 0.95:
                    items.append((
                        f"Gen {idx_str} near Pmax",
                        f"{pg_max[i]:.4f} / {pmax[i]:.4f} pu  ({100*pg_max[i]/pmax[i]:.1f}%)",
                        SEV_WARN,
                    ))
                # Lower limit
                if pg_min[i] < pmin[i] - 0.001 and pmin[i] > -50:
                    items.append((
                        f"Gen {idx_str} below Pmin",
                        f"{pg_min[i]:.4f}  <  {pmin[i]:.4f}  pu",
                        SEV_VIOL,
                    ))
        except Exception:
            pass

    # --- Line flow loading ---
    plf = results.get("plf")
    if plf is not None:
        try:
            line_idx = list(ss.Line.idx.v)
            rate = np.asarray(ss.Line.rate_a.v, dtype=float)
            plf_abs = _abs_max_over_time(plf)
            for i, idx_str in enumerate(line_idx):
                if i >= len(rate) or rate[i] <= 0 or rate[i] > 900:  # 999 sentinel
                    continue
                loading = 100.0 * plf_abs[i] / rate[i]
                if loading > 100.5:
                    items.append((
                        f"Line {idx_str} thermal",
                        f"{plf_abs[i]:.4f} / {rate[i]:.4f} pu  ({loading:.1f}%)",
                        SEV_VIOL,
                    ))
                elif loading > 90:
                    items.append((
                        f"Line {idx_str} loading",
                        f"{loading:.1f}%",
                        SEV_WARN,
                    ))
        except Exception:
            pass

    # --- Reserve margin (RTED only — has cru/crd) ---
    if results.get("routine") in {"RTED", "RTEDDG", "RTEDES", "RTEDESP", "RTEDVIS",
                                  "RTED2", "RTED2DG", "RTED2ES", "RTED2ESP"}:
        try:
            rtn = getattr(ss, results["routine"])
            for var, label in (("pru", "Total up-reserve"),
                               ("prd", "Total down-reserve")):
                if hasattr(rtn, var):
                    arr = np.asarray(getattr(rtn, var).v, dtype=float)
                    total = float(arr.sum())
                    items.append((label, f"{total:.4f} pu", SEV_OK))
        except Exception:
            pass

    if results.get("verification"):
        v = results["verification"]
        for name, value in v["residuals"].items():
            items.append((name, f"{value:.3e}", SEV_OK if value <= v["tolerance"] else SEV_VIOL))
        return items

    # --- Power balance residual ---
    try:
        pg_arr = np.asarray(results.get("pg", []), dtype=float)
        pq_p0 = np.asarray(ss.PQ.p0.v, dtype=float)
        if pg_arr.size:
            if pg_arr.ndim == 1:
                residual = float(pg_arr.sum() - pq_p0.sum())
            else:
                residual = float(pg_arr.sum(axis=0).mean() - pq_p0.sum())
            sev = SEV_OK if abs(residual) < 1e-3 else SEV_WARN if abs(residual) < 1e-1 else SEV_VIOL
            items.append(("Power balance residual", f"{residual:+.6f} pu", sev))
    except Exception:
        pass

    return items
