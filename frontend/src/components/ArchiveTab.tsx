import { useMemo, useState } from "react";
import {
  BarElement, CategoryScale, Chart as ChartJS, Legend, LinearScale, LineElement, PointElement, Tooltip,
} from "chart.js";
import { Bar, Line } from "react-chartjs-2";
import {
  api, type ForecastReport, type GateValue, type H4Cell, type Lang, type Level, type ModelSummary, type Station,
} from "../api";
import { useAsync } from "../hooks";
import {
  FEATURE_TEXT, GATE_TEXT, HIDDEN_DRIVERS, LEVEL_ICON, LEVELS, MONTHS_NOM, featureLabel, fmtDate, fmtInt, fmtNum,
  useT,
} from "../i18n";
import { GateMark, Kpi, LEVEL_COLOR, LevelBadge, Loading, Seg } from "./common";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, BarElement, Tooltip, Legend);

const TEST_YEARS = [2020, 2021, 2022, 2023, 2024, 2025];
const WARN: Level[] = ["orange", "red"];
// Одна запись десятичных чисел во всей панели (точка), как в таблицах.
const CHART_LOCALE = "en-US";

export default function ArchiveTab() {
  const { t, lang } = useT();
  const stations = useAsync((s) => api.stations(s), []);
  const report = useAsync((s) => api.forecastReport(s), []);
  const [station, setStation] = useState("aralsk");
  const [year, setYear] = useState(2024);
  const nm = (s: Station) => (lang === "kk" ? s.name_kk : s.name_ru);
  const byId = useMemo(() => new Map((stations.data ?? []).map((s) => [s.id, s])), [stations.data]);
  const stName = (id: string) => { const s = byId.get(id); return s ? nm(s) : id; };

  return (
    <>
      <div className="toolbar">
        <label>{t("station")}{" "}
          <select value={station} onChange={(e) => setStation(e.target.value)}>
            {(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{nm(s)}</option>)}
          </select>
        </label>
        <label>{t("year")}{" "}
          <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {TEST_YEARS.map((y) => <option key={y}>{y}</option>)}
          </select>
        </label>
        <span className="muted small">{t("arch_note")}</span>
      </div>
      <div className="grid2">
        <div className="card"><h3>{t("calendar")}</h3><Calendar station={station} year={year} /></div>
        <div className="card"><h3>{t("accuracy")}</h3><AccuracyBlock station={station} /></div>
      </div>
      <Loading error={report.error} loading={report.loading} />
      {report.data && <ReportBlocks r={report.data} station={station} stName={stName} />}
    </>
  );
}

interface Cell { i: number; date: string; level?: Level; p?: number; obs: boolean }

function Calendar({ station, year }: { station: string; year: number }) {
  const { t, lang } = useT();
  const cal = useAsync((s) => api.calendar(station, year, s), [station, year]);
  const [pick, setPick] = useState<Cell | null>(null);
  // 12 строк × 31 столбец: на телефоне ячейки крупнее, чем у «годовой ленты» 53×7.
  const months = useMemo(() => {
    if (!cal.data) return [];
    const ev = new Map(cal.data.events.map((e) => [e.date, e]));
    const fc = new Map(cal.data.forecasts_h1.map((f) => [f.day, f]));
    let i = 0;
    return Array.from({ length: 12 }, (_, m) => {
      const out: (Cell | null)[] = [];
      for (let d = 1; d <= 31; d++) {
        const dt = new Date(Date.UTC(year, m, d));
        if (dt.getUTCMonth() !== m) { out.push(null); continue; }
        const k = dt.toISOString().slice(0, 10);
        const f = fc.get(k);
        out.push({ i: i++, date: k, level: f?.level, p: f?.probability, obs: ev.get(k)?.dust === 1 });
      }
      return out;
    });
  }, [cal.data, year]);
  if (cal.error || cal.loading) return <Loading error={cal.error} loading={cal.loading} />;
  const all = months.flat().filter((c): c is Cell => c !== null);
  const isWarn = (c: Cell) => !!c.level && WARN.includes(c.level);
  const stat = (cs: Cell[]) => ({
    warn: cs.filter(isWarn).length, hit: cs.filter((c) => isWarn(c) && c.obs).length,
    obs: cs.filter((c) => c.obs).length, caught: cs.filter((c) => c.obs && isWarn(c)).length,
  });
  const total = stat(all);
  const summary = t("cal_summary", { year, ...total });
  const describe = (c: Cell) => `${fmtDate(c.date, lang)}: ${c.p !== undefined ? `${Math.round(c.p * 100)}%` : "—"}`
    + `${c.level ? ` ${LEVEL_ICON[c.level]}` : ""}${c.obs ? ` · ${t("dust_word")}` : ""}`;
  return (
    <>
      <p className="small">{summary}</p>
      <div className="calendar" role="img" aria-label={summary}
        onClick={(e) => {
          const i = (e.target as HTMLElement).dataset.i;
          if (i !== undefined) setPick(all[Number(i)] ?? null);
        }}>
        {months.map((row, m) => (
          <div className="cal-row" key={m}>
            <span className="cal-m" aria-hidden="true">{MONTHS_NOM[lang][m].slice(0, 3)}</span>
            {row.map((c, d) => c === null ? <i key={d} className="void" /> : (
              <i key={d} data-i={c.i} className={`${c.obs ? "obs" : ""} ${c.level ? `lv-${c.level}` : ""}`}
                style={{ background: c.level ? LEVEL_COLOR[c.level] : undefined }} title={describe(c)}>
                {c.level && WARN.includes(c.level) ? LEVEL_ICON[c.level] : ""}
              </i>
            ))}
          </div>
        ))}
      </div>
      <p className="small muted" aria-live="polite">{pick ? describe(pick) : t("cal_tap")}</p>
      <div className="legend small">
        {LEVELS.map((l) => <LevelBadge key={l} level={l} />)}
        <span><i className="sw ring" />{t("observed")}: {total.obs} {t("days_short")}</span>
      </div>
      <details>
        <summary className="small">{t("cal_months")}</summary>
        <div className="t-scroll">
          <table className="t">
            <thead><tr><th>{t("month")}</th><th>{t("warn_days")}</th><th>{t("dust_days")}</th><th>{t("caught_days")}</th></tr></thead>
            <tbody>{months.map((row, m) => {
              const s = stat(row.filter((c): c is Cell => c !== null));
              return <tr key={m}><td>{MONTHS_NOM[lang][m]}</td><td>{s.warn}</td><td>{s.obs}</td><td>{s.caught}</td></tr>;
            })}</tbody>
          </table>
        </div>
      </details>
    </>
  );
}

function AccuracyBlock({ station }: { station: string }) {
  const { t, lang } = useT();
  const [period, setPeriod] = useState("365d");
  const acc = useAsync((s) => api.accuracy({ settlement: station, period, horizon: 1 }, s), [station, period]);
  const a = acc.data;
  const archive = !a?.mode || a.mode === "backtest";
  return (
    <>
      <Seg label={t("period")} value={period} onChange={setPeriod}
        options={[{ value: "30d", label: t("days_n", { n: 30 }) },
          { value: "365d", label: t("days_n", { n: 365 }) },
          { value: "2500d", label: t("all_test") }]} />
      <Loading error={acc.error} loading={acc.loading} />
      {a && !a.n && <p className="muted">{t("acc_none")}</p>}
      {a && a.n > 0 && !a.events && <p className="muted">{t("acc_nodust")}</p>}
      {a && a.n > 0 && (
        <>
          <div className="kpis" style={{ marginTop: 10 }}>
            <Kpi value={`${a.orange_confirmed ?? 0}/${a.orange_alerts ?? 0}`} label={t("acc_confirmed")} />
            <Kpi value={fmtNum(a.POD)} label={t("pod")} />
            <Kpi value={fmtNum(a.FAR)} label={t("far")} />
            <Kpi value={fmtNum(a.pr_auc)} label="PR-AUC" />
            <Kpi value={fmtNum(a.brier, 3)} label="Brier" />
            <Kpi value={a.events ?? "—"} label={t("dust_days")} />
          </div>
          <p className="muted small">
            {lang === "kk" ? a.text_kk : a.text_ru}
            {/* Новый API сам называет период и источник в text_*; для старого добавляем подпись. */}
            {!a.mode && ` ${t(archive ? "acc_archive" : "acc_live", { from: fmtDate(a.from, lang), to: fmtDate(a.to, lang) })}`}
          </p>
        </>
      )}
    </>
  );
}

// Служебные поля рядом с воротами (не ворота): показываются в другом месте или как подпись.
const GATE_SKIP = new Set(["weather_inputs", "H3_delta_pr_auc", "H3_delta_pr_auc_ci", "H3_delta_bss_ci", "ece_quantile"]);

function gateLabel(k: string, lang: Lang) { return GATE_TEXT[k]?.[lang] ?? k; }

function Gates({ gates }: { gates: Record<string, GateValue> }) {
  const { t, lang } = useT();
  const ci = gates.H3_delta_pr_auc_ci;
  return (
    <ul className="gates">
      {Object.entries(gates).filter(([k, v]) => !GATE_SKIP.has(k) && (typeof v === "boolean" || typeof v === "string")).map(([k, v]) => (
        <li className="gate" key={k}>
          {typeof v === "boolean" ? <GateMark ok={v} /> : null}
          <span>{gateLabel(k, lang)}{typeof v === "string" ? `: ${v}` : ""}
            {k === "H3_source_features_help" && Array.isArray(ci) && ci.length === 2
              ? <span className="muted"> ({t("gate_ci", { lo: fmtNum(ci[0], 3), hi: fmtNum(ci[1], 3) })})</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ReportBlocks({ r, station, stName }: { r: ForecastReport; station: string; stName: (id: string) => string }) {
  const { t, lang } = useT();
  const [h, setH] = useState<"h0" | "h1" | "h2">("h1");
  const hr = r[h];
  const best = Object.entries(hr.models).sort((a, b) => b[1].pr_auc - a[1].pr_auc)[0]?.[0];
  const series = r.replay?.series?.[station]?.h1 ?? [];
  const evDrivers = (r.replay?.series?.[station]?.drivers_h1 ?? [])
    .filter((d) => !HIDDEN_DRIVERS.has(d.feature) && Number.isFinite(d.contribution));
  const evMax = Math.max(0.01, ...evDrivers.map((d) => Math.abs(d.contribution)));
  const shap = Object.entries(r.h1.shap_top);
  const seen = new Map<string, number>();
  for (const [k] of shap) { const l = featureLabel(k, lang); seen.set(l, (seen.get(l) ?? 0) + 1); }
  const shapLabel = (k: string) => {
    const l = featureLabel(k, lang);
    return (seen.get(l) ?? 0) > 1 || !FEATURE_TEXT[k] ? `${l} (${k})` : l;
  };
  const gates = r.gates[h] ?? {};
  const upper = h !== "h0" && (hr.weather_inputs === undefined || /upper|era5/i.test(hr.weather_inputs));
  const splitName: Record<string, string> = { train: t("split_train"), valid: t("split_valid"), test: t("split_test") };
  return (
    <>
      <div className="card">
        <div className="row between wrap">
          <h3>{t("models_vs")}</h3>
          <Seg label={t("horizon")} value={h} onChange={setH}
            options={[{ value: "h0", label: t("today") }, { value: "h1", label: t("tomorrow") }, { value: "h2", label: t("after") }]} />
        </div>
        {upper && <p className="banner info small">{t("upper_bound")}</p>}
        <div className="t-scroll">
          <table className="t">
            <thead><tr><th scope="col">{t("model")}</th><th>PR-AUC</th><th>ROC-AUC</th><th>Brier</th><th>BSS</th><th>ECE</th></tr></thead>
            <tbody>
              {Object.entries(hr.models).map(([n, m]) => (
                <tr key={n} className={n === best ? "best" : ""}>
                  <td>{n}</td>
                  <td>{fmtNum(m.pr_auc, 3)}{m.pr_auc_ci ? ` (${fmtNum(m.pr_auc_ci[0], 3)}–${fmtNum(m.pr_auc_ci[1], 3)})` : ""}</td>
                  <td>{fmtNum(m.roc_auc, 3)}</td><td>{fmtNum(m.brier, 4)}</td>
                  <td>{fmtNum(m.bss, 3)}</td><td>{fmtNum(m.ece, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">{t("models_note")}{" "}
          {hr.rule_B2.dry_days > 0 ? t("rule_b2", { g: hr.rule_B2.gust_ms, d: hr.rule_B2.dry_days })
            : t("rule_b2_nodry", { g: hr.rule_B2.gust_ms })}
        </p>
        <p className="muted small">{t("split_title")}: {Object.entries(hr.split)
          .map(([k, v]) => t("split_row", { name: splitName[k] ?? k, y0: v[0], y1: v[1], n: fmtInt(v[2]), ev: fmtInt(v[3]) }))
          .join(" · ")}</p>
        <h3>{t("gates")}</h3>
        <Gates gates={gates} />
      </div>
      <div className="grid2">
        <div className="card">
          <h3>{t("replay")}: {fmtDate(r.replay?.event_date, lang)} {r.replay?.event_date?.slice(0, 4)}</h3>
          {series.length > 0 ? (
            <Line height={220} data={{
              labels: series.map((s) => fmtDate(s.date, lang)),
              datasets: [
                { label: t("replay_fc"), data: series.map((s) => s.p * 100),
                  borderColor: "#9c4f22", backgroundColor: "#9c4f22", tension: 0 },
                { label: t("replay_obs"),
                  data: series.map((s) => s.y * 100), borderColor: "#1f1c18", backgroundColor: "#1f1c18",
                  showLine: false, pointStyle: "rectRot", pointRadius: 6 },
              ],
            }} options={{ locale: CHART_LOCALE,
              scales: { y: { min: 0, max: 105, ticks: { stepSize: 20 }, title: { display: true, text: t("probability_pct") } } },
              plugins: { legend: { position: "bottom" } } }} />
          ) : <p className="muted">—</p>}
          <p className="muted small">{t("replay_note", { n: r.replay?.stations_with_dust ?? "—" })}</p>
          {evDrivers.length > 0 && (
            <>
              <h3>{t("replay_drivers", { date: fmtDate(r.replay?.event_date, lang), st: stName(station) })}</h3>
              <ul className="drivers">
                {evDrivers.map((d) => (
                  <li key={d.feature}>
                    <span>{d.contribution >= 0 ? "↑" : "↓"} {featureLabel(d.feature, lang)}</span>
                    <span className="bar"><i className={d.contribution >= 0 ? "up" : "down"}
                      style={{ width: `${(Math.abs(d.contribution) / evMax) * 100}%` }} /></span>
                  </li>
                ))}
              </ul>
              <p className="muted small">{t("replay_drivers_note")}</p>
            </>
          )}
        </div>
        <div className="card">
          <h3>{t("explain")}</h3>
          <Bar height={220} data={{ labels: shap.map(([k]) => shapLabel(k)),
            datasets: [{ label: "mean |SHAP|", data: shap.map(([, v]) => v), backgroundColor: "#9c4f22" }] }}
            options={{ locale: CHART_LOCALE, indexAxis: "y", plugins: { legend: { display: false } } }} />
        </div>
      </div>
      <div className="grid2">
        <div className="card"><h3>{t("h4_title")}</h3><H4Block r={r} /></div>
        <div className="card"><h3>{t("cams_title")}</h3><CamsBlock r={r} stName={stName} /></div>
      </div>
    </>
  );
}

const fmtP = (p?: number) => (p === undefined || p === null || Number.isNaN(p) ? "—" : p < 0.001 ? p.toExponential(1) : p.toFixed(3));

function H4Block({ r }: { r: ForecastReport }) {
  const { t, lang } = useT();
  const rows = (["lag_0", "lag_1", "lag_2"] as const)
    .map((k) => [k, r.H4[k]] as const)
    .filter((x): x is readonly [typeof x[0], { all?: H4Cell; nw_w_wind?: H4Cell; significant?: boolean }] => typeof x[1] === "object" && x[1] !== null);
  const concl = (lang === "kk" ? r.H4.conclusion_kk : r.H4.conclusion_ru) ?? r.H4.conclusion;
  return (
    <>
      {concl && <p lang={(lang === "kk" && r.H4.conclusion_kk) || lang === "ru" ? lang : "ru"}><b>{concl}</b></p>}
      <div className="t-scroll">
        <table className="t">
          <thead><tr><th>{t("h4_lag")}</th><th>OR</th><th>p</th><th>{t("h4_p_kz")}</th><th>{t("h4_p_kz_ar")}</th><th>{t("h4_or_nw")}</th><th>{t("h4_sig")}</th></tr></thead>
          <tbody>{rows.map(([k, c]) => (
            <tr key={k}><td>{t("h4_days", { n: k.slice(-1) })}</td>
              <td>{fmtNum(c.all?.mh_or ?? c.all?.odds_ratio)}</td>
              <td>{fmtP(c.all?.p_perm ?? c.all?.p_value)}</td>
              <td>{fmtNum(c.all?.["P(kyzylorda)"], 3)}</td><td>{fmtNum(c.all?.["P(kyzylorda|aralsk)"], 3)}</td>
              <td>{fmtNum(c.nw_w_wind?.mh_or ?? c.nw_w_wind?.odds_ratio)}</td>
              <td>{c.significant === undefined ? "—" : <GateMark ok={c.significant} />}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <p className="muted small">{t(rows.some(([, c]) => c.all?.mh_or !== undefined) ? "h4_note_mh" : "h4_note")}</p>
    </>
  );
}

function CamsBlock({ r, stName }: { r: ForecastReport; stName: (id: string) => string }) {
  const { t, lang } = useT();
  const b = r.B3_cams;
  const op = r.operational;
  const note = (x?: { note?: string; note_ru?: string; note_kk?: string }) =>
    x ? (lang === "kk" ? x.note_kk : x.note_ru) ?? (lang === "ru" ? x.note : undefined) : undefined;
  const row = (name: string, m?: ModelSummary) => m && (
    <tr key={name}><td>{name}</td><td>{fmtNum(m.pr_auc, 3)}</td><td>{fmtNum(m.brier, 4)}</td><td>{fmtNum(m.bss, 3)}</td></tr>
  );
  return (
    <>
      {b.available ? (
        <>
          <div className="t-scroll">
            <table className="t">
              <thead><tr><th></th><th>PR-AUC</th><th>Brier</th><th>BSS</th></tr></thead>
              <tbody>{row("AralShield M2", b.model)}{row("CAMS (B3)", b.cams)}</tbody>
            </table>
          </div>
          <p className="muted small">{b.period}. {note(b)} {t("cams_caveat")}</p>
        </>
      ) : <p className="muted" lang="ru">{b.error}</p>}
      {op?.available && (
        <>
          <h3>{t("oper_title")}</h3>
          <div className="t-scroll">
            <table className="t">
              <thead><tr><th></th><th>PR-AUC</th><th>Brier</th><th>BSS</th></tr></thead>
              <tbody>
                {row(t("oper_era5"), op.reanalysis)}
                {row(t("oper_fc"), op.operational)}
                {row(t("oper_b2"), op.rule_B2_operational)}
              </tbody>
            </table>
          </div>
          <p className="muted small">{op.period}. {note(op)}
            {op.excluded_stations && op.excluded_stations.length > 0
              ? ` ${t("oper_excluded", { list: op.excluded_stations.map(stName).join(", ") })}` : ""}</p>
        </>
      )}
    </>
  );
}
