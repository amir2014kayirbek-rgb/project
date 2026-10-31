"""Метрики вероятностного прогноза (раздел 13.5)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score


def brier(p, o) -> float:
    p, o = np.asarray(p, float), np.asarray(o, float)
    return float(np.mean((p - o) ** 2))


def bss(p, o, p_ref) -> float:
    ref = brier(p_ref, o)
    return float(1 - brier(p, o) / ref) if ref > 0 else float("nan")


def pr_auc(p, o) -> float:
    o = np.asarray(o)
    return float(average_precision_score(o, p)) if 0 < o.sum() < len(o) else float("nan")


def roc_auc(p, o) -> float:
    o = np.asarray(o)
    return float(roc_auc_score(o, p)) if 0 < o.sum() < len(o) else float("nan")


def ece(p, o, bins: int = 10) -> float:
    """Ожидаемая ошибка калибровки."""
    p, o = np.asarray(p, float), np.asarray(o, float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(p[m].mean() - o[m].mean())
    return float(total)


def ece_quantile(p, o, bins: int = 10) -> float:
    """ECE по квантильным корзинам (равное число прогнозов в корзине): для редкого события
    равноширинные корзины почти все прогнозы кладут в [0; 0,1) и ECE получается заниженной."""
    p, o = np.asarray(p, float), np.asarray(o, float)
    if len(p) == 0:
        return float("nan")
    order = np.argsort(p, kind="stable")
    total = 0.0
    for idx in np.array_split(order, min(bins, len(p))):
        if len(idx):
            total += len(idx) / len(p) * abs(p[idx].mean() - o[idx].mean())
    return float(total)


def reliability(p, o, bins: int = 10) -> pd.DataFrame:
    p, o = np.asarray(p, float), np.asarray(o, float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        if m.sum() >= 5:
            rows.append({"bin": b, "p_mean": p[m].mean(), "o_freq": o[m].mean(), "n": int(m.sum())})
    return pd.DataFrame(rows)


def contingency(p, o, thr: float) -> dict:
    p, o = np.asarray(p, float), np.asarray(o, int)
    yes = p >= thr
    tp = int((yes & (o == 1)).sum())
    fp = int((yes & (o == 0)).sum())
    fn = int((~yes & (o == 1)).sum())
    tn = int((~yes & (o == 0)).sum())
    pod = tp / (tp + fn) if tp + fn else float("nan")
    far = fp / (tp + fp) if tp + fp else float("nan")
    csi = tp / (tp + fp + fn) if tp + fp + fn else float("nan")
    return {"threshold": thr, "TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "POD": pod, "FAR": far, "CSI": csi}


def block_bootstrap(dates, fn, n: int = 300, seed: int = 42) -> tuple[float, float]:
    """95% интервал метрики блочным бутстрепом по неделям (учёт автокорреляции)."""
    rng = np.random.default_rng(seed)
    weeks = pd.to_datetime(pd.Series(dates)).dt.to_period("W").astype(str).to_numpy()
    uniq = np.unique(weeks)
    groups = {w: np.flatnonzero(weeks == w) for w in uniq}
    vals = []
    for _ in range(n):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([groups[w] for w in pick])
        v = fn(idx)
        if not np.isnan(v):
            vals.append(v)
    if not vals:
        return float("nan"), float("nan")
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def paired_delta(p_a, p_b, o, p_clim, dates, n: int = 300) -> dict:
    """Парное сравнение двух прогнозов на одних строках: Δ = A − B для PR-AUC и BSS
    с 95% блочным бутстрепом по неделям (одни и те же недели для обеих моделей)."""
    p_a, p_b, o, p_clim = (np.asarray(x, float) for x in (p_a, p_b, o, p_clim))
    o = o.astype(int)
    return {
        "delta_pr_auc": pr_auc(p_a, o) - pr_auc(p_b, o),
        "delta_pr_auc_ci": block_bootstrap(dates, lambda i: pr_auc(p_a[i], o[i]) - pr_auc(p_b[i], o[i]), n=n),
        "delta_bss": bss(p_a, o, p_clim) - bss(p_b, o, p_clim),
        "delta_bss_ci": block_bootstrap(dates, lambda i: bss(p_a[i], o[i], p_clim[i]) - bss(p_b[i], o[i], p_clim[i]), n=n),
    }


def summary(p, o, p_clim, dates, with_ci: bool = True) -> dict:
    p, o, p_clim = np.asarray(p, float), np.asarray(o, int), np.asarray(p_clim, float)
    res = {
        "n": int(len(o)), "events": int(o.sum()),
        "pr_auc": pr_auc(p, o), "roc_auc": roc_auc(p, o),
        "brier": brier(p, o), "bss": bss(p, o, p_clim), "ece": ece(p, o), "ece_q": ece_quantile(p, o),
    }
    if with_ci:
        res["pr_auc_ci"] = block_bootstrap(dates, lambda i: pr_auc(p[i], o[i]))
        res["bss_ci"] = block_bootstrap(dates, lambda i: bss(p[i], o[i], p_clim[i]))
    return res
