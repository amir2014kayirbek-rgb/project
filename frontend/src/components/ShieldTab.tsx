import { useEffect, useMemo, useState } from "react";
import { CircleMarker, GeoJSON, Tooltip } from "react-leaflet";
import type { Layer, PathOptions } from "leaflet";
import {
  ApiError, api, PRIORITY_LIMIT, type Distribution, type Plan, type PriorityProps, type ScenarioResult,
  type ShieldReport,
} from "../api";
import { useAsync, useDebounced } from "../hooks";
import { SHIELD_CLASS_TEXT, fmtInt, fmtNum, useT, type Key } from "../i18n";
import { BaseMap, GateMark, Loading, Seg } from "./common";

const CLASS_COLOR: Record<string, string> = {
  "надёжный высокий": "#7a1f12", "высокий": "#c0392b", "средний": "#e8a33d", "низкий": "#f3d9a4",
};
const SHOWN_CLASSES = ["надёжный высокий", "высокий", "средний"];
const PLAN_KEY: Record<Plan, Key> = {
  rating: "plan_rating", random: "plan_random", nearest: "plan_nearest", custom: "plan_custom",
};
const TOP_ROWS = 30;
const AREA_HA = 1000;

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

interface PlanResult { plan: Plan; r: ScenarioResult; dist?: Distribution }

/** Результат из отчёта «Щита» (comparison_1000ha) — показывается сразу, без запроса к API. */
function fromReport(rep: ShieldReport | null): { a: PlanResult; b: PlanResult } | null {
  const c = rep?.comparison_1000ha as (ShieldReport["comparison_1000ha"] & {
    nearest_suitable?: ScenarioResult; random_suitable?: Distribution }) | undefined;
  if (!c?.by_rating) return null;
  const a: PlanResult = { plan: "rating", r: c.by_rating };
  if (c.nearest_suitable) return { a, b: { plan: "nearest", r: c.nearest_suitable } };
  const mean = c.random_suitable?.mean ?? c.random_suitable_people_weighted_mean;
  if (mean === undefined) return null;
  return { a, b: { plan: "random", dist: c.random_suitable,
    r: { ...c.by_rating, people_weighted_reduction: mean, total_reduction_pct: NaN, per_settlement: [] } } };
}

export default function ShieldTab() {
  const { t, lang } = useT();
  const [minSuitInput, setMinSuitInput] = useState(0);
  const minSuit = useDebounced(minSuitInput, 350);
  const cells = useAsync((s) => api.priorities(minSuit, s), [minSuit]);
  const report = useAsync((s) => api.shieldReport(s), []);
  const settlements = useAsync((s) => api.settlements(s), []);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [planB, setPlanB] = useState<Plan>("nearest");
  const [result, setResult] = useState<{ a: PlanResult; b: PlanResult } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [dl, setDl] = useState(false);

  const names = useMemo(() => new Map((settlements.data ?? []).map((s) => [s.id, lang === "kk" ? s.name_kk : s.name_ru])),
    [settlements.data, lang]);
  const features = cells.data?.features ?? [];
  // Новый ключ слоя — только при новых данных или языке (не при каждом клике по ячейке).
  const dataKey = useMemo(() => Math.random().toString(36).slice(2), [cells.data]);

  // Выбор ограничен ячейками, которые сейчас загружены на карту.
  useEffect(() => {
    if (!cells.data) return;
    const ids = new Set(cells.data.features.map((f) => f.properties.cell_id));
    setSelected((s) => {
      const n = new Set([...s].filter((id) => ids.has(id)));
      return n.size === s.size ? s : n;
    });
  }, [cells.data]);

  const toggle = (id: number) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  const style = useMemo(() => (f?: GeoJSON.Feature<GeoJSON.Geometry, PriorityProps>): PathOptions => {
    const p = f!.properties;
    const sel = selected.has(p.cell_id);
    return { color: sel ? "#1e63b5" : CLASS_COLOR[p.class] ?? "#999", weight: sel ? 2.5 : 0.3,
      fillColor: CLASS_COLOR[p.class] ?? "#999", fillOpacity: 0.35 + 0.6 * p.priority };
  }, [selected]);

  const clsName = (c: string) => SHIELD_CLASS_TEXT[c]?.[lang] ?? c;
  const protName = (id: string) => (id ? names.get(id) ?? id : "—");

  const runPlan = async (plan: Plan): Promise<PlanResult> => {
    const r = await api.scenario({ plan, area_ha: AREA_HA, cell_ids: plan === "custom" ? [...selected] : undefined });
    return { plan, r, dist: r.distribution ?? r.people_weighted_reduction_dist };
  };
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const a = await runPlan("rating");
      let b: PlanResult;
      try {
        b = await runPlan(planB);
      } catch (e) {
        // Старый API не знает плана nearest — берём его из отчёта «Щита», если он там есть.
        const rep = fromReport(report.data);
        if (planB === "nearest" && e instanceof ApiError && e.status === 422 && rep?.b.plan === "nearest") b = rep.b;
        else throw e;
      }
      setResult({ a, b });
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const download = async (fmt: string) => {
    setErr(null); setDl(true);
    try {
      const r = await fetch(api.exportUrl(fmt), { headers: { "X-API-Key": key } });
      if (!r.ok) { setErr(`${r.status}: ${(await r.json().catch(() => ({}))).detail ?? r.statusText}`); return; }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `aralshield_priority_top.${fmt === "shp" ? "zip" : fmt}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally { setDl(false); }
  };

  const onEach = (f: GeoJSON.Feature<GeoJSON.Geometry, PriorityProps>, layer: Layer) => {
    const p = f.properties;
    layer.bindTooltip(`${esc(clsName(p.class))} · ${t("priority")} ${fmtNum(p.priority)}<br>`
      + `${t("suitability")} ${fmtNum(p.suitability)} · ${t("stability")} ${fmtNum(p.stability)}<br>`
      + `${t("protects")}: ${esc(protName(p.protects))} (${fmtInt(p.protected_population)})`);
    layer.on("click", () => toggle(p.cell_id));
  };

  const rep = report.data;
  const shown = result ?? fromReport(rep);
  const total = cells.data?.total_matching
    ?? (rep ? SHOWN_CLASSES.reduce((a, c) => a + (rep.class_counts?.[c] ?? 0), 0) : undefined);
  const overlap = rep?.sensitivity.top_overlap_mean ?? rep?.top_overlap_mean;
  return (
    <div className="split">
      <div className="mapwrap">
        <BaseMap center={[45.9, 60.8]} zoom={7}>
          {cells.data && (
            <GeoJSON key={`${dataKey}-${lang}-${settlements.data ? 1 : 0}`} data={cells.data}
              style={style as (f?: GeoJSON.Feature) => PathOptions}
              onEachFeature={onEach as (f: GeoJSON.Feature, l: Layer) => void} />
          )}
          {(settlements.data ?? []).map((s) => (
            <CircleMarker key={s.id} center={[s.lat, s.lon]} radius={4 + Math.log10(s.population)}
              pathOptions={{ color: "#1f1c18", weight: 1, fillColor: "#fff", fillOpacity: 1 }}>
              <Tooltip>{lang === "kk" ? s.name_kk : s.name_ru} · {fmtInt(s.population)}</Tooltip>
            </CircleMarker>
          ))}
        </BaseMap>
      </div>
      <aside className="panel" id="side" tabIndex={-1}>
        <h2>{t("shield_title")}</h2>
        <div className="legend col">
          {SHOWN_CLASSES.map((k) => (
            <span key={k}><i className="sw" style={{ background: CLASS_COLOR[k] }} />{clsName(k)}
              {rep?.class_counts?.[k] ? ` — ${fmtInt(rep.class_counts[k])}` : ""}</span>
          ))}
        </div>
        <label className="small">{t("min_suit")} <output>{minSuitInput.toFixed(1)}</output>{" "}
          <input type="range" min={0} max={0.9} step={0.1} value={minSuitInput}
            onChange={(e) => setMinSuitInput(Number(e.target.value))} />
        </label>
        <Loading error={cells.error} loading={cells.loading} />
        {cells.data && (
          <p className="small muted">{t("shown_of", { n: fmtInt(features.length), m: fmtInt(Math.max(total ?? 0, features.length)) })}
            {features.length >= PRIORITY_LIMIT ? ` (≤ ${fmtInt(PRIORITY_LIMIT)})` : ""}</p>
        )}
        {rep && (
          <div className="small">
            <p className="gate"><GateMark ok={rep.gate_rank_stability_ge_0_7} /><b>{t("tau_gate")}</b></p>
            <p className="muted">{t("cell_km", { k: rep.cell_km })} · {t("tau_detail", {
              tau: fmtNum(rep.sensitivity.kendall_tau_top_mean), min: fmtNum(rep.sensitivity.kendall_tau_top_min),
              n: rep.sensitivity.scenarios, top: rep.sensitivity.top_n, ov: fmtNum(overlap),
              robust: fmtInt(rep.sensitivity.robust_cells) })}</p>
            {!rep.gate_rank_stability_ge_0_7 && <p className="err">{t("tau_fail_note")}</p>}
          </div>
        )}
        <h3>{t("whatif")}</h3>
        <div className="row wrap">
          <span className="small">{t("compare")}</span>
          <Seg label={t("compare")} value={planB} onChange={setPlanB}
            options={(["nearest", "random", "custom"] as Plan[]).map((p) => ({
              value: p, label: p === "custom" ? `${t(PLAN_KEY[p])} (${selected.size})` : t(PLAN_KEY[p]),
              disabled: p === "custom" && selected.size === 0 }))} />
          <button className="btn" disabled={busy || (planB === "custom" && selected.size === 0)} onClick={run}>
            {busy ? t("loading") : "▶"}<span className="sr">{t("compare")}</span>
          </button>
          <button className="btn ghost" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>{t("clear")}</button>
        </div>
        <p className="muted small">{t("select_hint")}</p>
        {err && <p className="err" role="alert">{err}</p>}
        {shown && <Compare a={shown.a} b={shown.b} />}
        <details className="top-cells">
          <summary>{t("top_cells")}</summary>
          <div className="t-scroll">
            <table className="t">
              <thead><tr><th>{t("rank")}</th><th>{t("cls")}</th><th>{t("priority")}</th><th>{t("protects")}</th><th></th></tr></thead>
              <tbody>{features.slice(0, TOP_ROWS).map((f, i) => {
                const p = f.properties;
                return (
                  <tr key={p.cell_id}><td>{i + 1}</td><td>{clsName(p.class)}</td><td>{fmtNum(p.priority)}</td>
                    <td>{protName(p.protects)}</td>
                    <td><input type="checkbox" checked={selected.has(p.cell_id)} onChange={() => toggle(p.cell_id)}
                      aria-label={t("select_cell", { id: p.cell_id })} /></td></tr>
                );
              })}</tbody>
            </table>
          </div>
        </details>
        <h3>{t("export")}</h3>
        <div className="row wrap">
          <label className="small">{t("api_key")}{" "}
            <input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
          </label>
          {["geojson", "kml", "shp"].map((f) => (
            <button key={f} className="btn ghost" disabled={!key || dl} onClick={() => download(f)}>{f.toUpperCase()}</button>
          ))}
        </div>
        <p className="muted small" aria-live="polite">{dl ? t("export_busy") : !key ? t("export_hint") : ""}</p>
      </aside>
    </div>
  );
}

function Compare({ a, b }: { a: PlanResult; b: PlanResult }) {
  const { t, lang } = useT();
  const pw = (x: PlanResult) => x.dist?.mean ?? x.r.people_weighted_reduction;
  const ratio = pw(b) > 0 ? pw(a) / pw(b) : null;
  const area = (x: PlanResult) => t("plan_area", { ha: fmtInt(x.r.area_ha), cells: x.r.cells });
  const perB = new Map(b.r.per_settlement.map((p) => [p.settlement, p.reduction_pct]));
  const rows = a.r.per_settlement.filter((p) => p.reduction_pct > 0.005 || (perB.get(p.settlement) ?? 0) > 0.005)
    .sort((x, y) => y.reduction_pct - x.reduction_pct);
  return (
    <div className="card" style={{ marginTop: 10 }}>
      <div className="t-scroll">
        <table className="t">
          <thead><tr><th></th><th>{t(PLAN_KEY[a.plan])}</th><th>{t(PLAN_KEY[b.plan])}</th></tr></thead>
          <tbody>
            <tr><td>{t("area")}</td><td>{area(a)}</td><td>{area(b)}</td></tr>
            <tr><td>{t("reduction")}, %</td><td>{fmtNum(a.r.total_reduction_pct, 3)}</td><td>{fmtNum(b.r.total_reduction_pct, 3)}</td></tr>
            <tr><td>{t("people_eq")}</td><td>{fmtInt(pw(a))}</td>
              <td>{fmtInt(pw(b))}{b.dist ? <div className="muted small">{t("random_dist", { p5: fmtInt(b.dist.p5), p95: fmtInt(b.dist.p95) })}</div> : null}</td></tr>
          </tbody>
        </table>
      </div>
      {ratio !== null && Number.isFinite(ratio) && (
        <p><b>{t("ratio", { x: ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1), plan: t(PLAN_KEY[b.plan]) })}</b></p>
      )}
      {rows.length > 0 && (
        <div className="t-scroll">
          <table className="t" style={{ marginTop: 8 }}>
            <thead><tr><th>{t("settlement")}</th><th>{t("population")}</th><th>−% ({t(PLAN_KEY[a.plan])})</th>
              <th>−% ({t(PLAN_KEY[b.plan])})</th></tr></thead>
            <tbody>{rows.map((p) => (
              <tr key={p.settlement}><td>{lang === "kk" ? p.name_kk : p.name_ru}</td>
                <td>{fmtInt(p.population)}</td><td>{fmtNum(p.reduction_pct)}</td>
                <td>{perB.has(p.settlement) ? fmtNum(perB.get(p.settlement)) : "—"}</td></tr>))}</tbody>
          </table>
        </div>
      )}
      <p className="muted small">{t("area_note")} {t("scenario_note")}</p>
    </div>
  );
}
