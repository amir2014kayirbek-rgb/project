import { useEffect, useRef, useState } from "react";
import { ApiError, api, demoEvent, type Health, type Lang, type PreviewParams } from "../api";
import { useAsync } from "../hooks";
import { ROLES, fmtDate, useT, type Key } from "../i18n";
import { GateMark, Loading } from "./common";

const FIGURES: { id: string; key: Key }[] = [
  { id: "pr_auc_models", key: "fig_pr_auc_models" },
  { id: "pr_reliability_h1", key: "fig_pr_reliability_h1" },
  { id: "shap_importance_h1", key: "fig_shap_importance_h1" },
];

/** SMS ниже оранжевого не отправляется: API возвращает заглушку в скобках (или 404) — показываем свой текст. */
const isSmsPlaceholder = (s: string) => /^\(.*\)$/s.test(s.trim());

async function fetchPreview(p: PreviewParams, signal: AbortSignal): Promise<string> {
  try {
    return await api.preview(p, signal);
  } catch (e) {
    if (p.channel === "sms" && e instanceof ApiError && e.status === 404) return "";
    throw e;
  }
}

function Preview({ p, title }: { p: PreviewParams; title?: string }) {
  const { t } = useT();
  const q = useAsync((s) => fetchPreview(p, s), [p.settlement, p.horizon, p.role, p.lang, p.channel, p.mode, p.issue_date]);
  const sms = p.channel === "sms";
  const none = sms && q.data !== null && (q.data === "" || isSmsPlaceholder(q.data));
  return (
    <div>
      {title && <p className="small muted">{title}</p>}
      <pre className={`msg ${sms ? "" : "tg"}`} lang={p.lang} aria-busy={q.loading}>
        {q.loading ? t("loading") : none ? t("sms_none") : q.data ?? q.error ?? "…"}
      </pre>
      {sms && q.data && !none && <p className="muted small">{t("sms_count", { n: q.data.length })}</p>}
    </div>
  );
}

export default function ReportsTab() {
  const { t, lang } = useT();
  const summary = useAsync((s) => api.summary(lang, s), [lang]);
  const stations = useAsync((s) => api.stations(s), []);
  const health = useAsync((s) => api.health(s), []);
  const report = useAsync((s) => api.forecastReport(s), []);
  const [st, setSt] = useState("aralsk");
  const [role, setRole] = useState("teacher");
  const [h, setH] = useState(1);
  const [ch, setCh] = useState("telegram");
  const [demo, setDemo] = useState(false);
  const [fig, setFig] = useState<string | null>(null);
  const ev = demoEvent(report.data);
  const other: Lang = lang === "kk" ? "ru" : "kk";
  const stName = (id: string) => {
    const s = (stations.data ?? []).find((x) => x.id === id);
    return s ? (lang === "kk" ? s.name_kk : s.name_ru) : id;
  };
  const base: PreviewParams = demo && ev
    ? { settlement: ev.station, horizon: 1, role, lang, channel: "telegram", mode: "backtest", issue_date: ev.issue }
    : { settlement: st, horizon: h, role, lang, channel: ch };

  return (
    <>
      <div className="grid2">
        <div className="card">
          <h3>{t("summary")}</h3>
          <Loading error={summary.error} loading={summary.loading} />
          {summary.data && <pre className="msg">{summary.data}</pre>}
          <button className="btn" onClick={() => window.print()}>{t("print")}</button>
        </div>
        <div className="card">
          <h3>{t("bot_preview")}</h3>
          <label className="small demo-toggle">
            <input type="checkbox" checked={demo && !!ev} disabled={!ev} onChange={(e) => setDemo(e.target.checked)} />{" "}
            {t("demo", { date: ev ? `${fmtDate(ev.event, lang)} ${ev.event.slice(0, 4)}` : "…" })}
          </label>
          <div className="row wrap">
            <label className="small">{t("station")}{" "}
              <select value={demo && ev ? ev.station : st} disabled={demo} onChange={(e) => setSt(e.target.value)}>
                {(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{lang === "kk" ? s.name_kk : s.name_ru}</option>)}
              </select>
            </label>
            <label className="small">{t("role")}{" "}
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                {ROLES.map((r) => <option key={r.id} value={r.id}>{lang === "kk" ? r.kk : r.ru}</option>)}
              </select>
            </label>
            <label className="small">{t("horizon")}{" "}
              <select value={demo ? 1 : h} disabled={demo} onChange={(e) => setH(Number(e.target.value))}>
                <option value={0}>{t("today")}</option><option value={1}>{t("tomorrow")}</option><option value={2}>{t("after")}</option>
              </select>
            </label>
            {!demo && (
              <label className="small">{t("channel")}{" "}
                <select value={ch} onChange={(e) => setCh(e.target.value)}>
                  <option value="telegram">Telegram</option><option value="sms">SMS</option>
                </select>
              </label>
            )}
          </div>
          {demo && ev ? (
            <>
              <p className="banner info small">{t("demo_note", { issue: fmtDate(ev.issue, lang), st: stName(ev.station) })}</p>
              <Preview p={base} title="Telegram" />
              <Preview p={{ ...base, lang: other }} title={`Telegram · ${other === "kk" ? "ҚАЗ" : "РУС"}`} />
              <Preview p={{ ...base, channel: "sms" }} title="SMS" />
            </>
          ) : <Preview p={base} />}
        </div>
      </div>
      <div className="card">
        <h3>{t("figures")}</h3>
        <p className="muted small">{t("figures_note")}</p>
        <div className="figs">
          {FIGURES.map((f) => (
            <figure key={f.id}>
              <button className="fig-btn" onClick={() => setFig(f.id)} aria-label={t(f.key)}>
                <img src={`/figures/${f.id}.png`} alt={t(f.key)} loading="lazy" />
              </button>
              <figcaption className="small muted">{t(f.key)}</figcaption>
            </figure>
          ))}
        </div>
      </div>
      {fig && <Lightbox src={`/figures/${fig}.png`} alt={t(FIGURES.find((x) => x.id === fig)!.key)} onClose={() => setFig(null)} />}
      <div className="card">
        <h3>{t("health")}</h3>
        <Loading error={health.error} loading={health.loading} />
        {health.data && <HealthRows h={health.data} stName={stName} />}
      </div>
    </>
  );
}

function Lightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  const { t } = useT();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  return (
    <dialog ref={ref} className="lightbox" onClose={onClose} onClick={(e) => { if (e.target === ref.current) onClose(); }}
      aria-label={alt}>
      <button className="btn ghost close" onClick={onClose} autoFocus>{t("close")} ✕</button>
      <img src={src} alt={alt} />
    </dialog>
  );
}

function HealthRows({ h, stName }: { h: Health; stName: (id: string) => string }) {
  const { t, lang } = useT();
  const status = h.status === "ok" ? "st_ok" : h.status === "stale" ? "st_stale" : "st_degraded";
  const models: Record<string, string> = { forecast: lang === "kk" ? "«Болжам» M2" : "«Прогноз» M2", eye: lang === "kk" ? "«Көз»" : "«Глаз»",
    shield: lang === "kk" ? "«Қалқан»" : "«Щит»" };
  return (
    <dl className="kv health">
      <dt>{t("h_status")}</dt>
      <dd><span className={`status st-${h.status}`}>{h.status === "ok" ? "●" : "▲"} {t(status)}</span></dd>
      <dt>{t("h_last")}</dt><dd>{fmtDate(h.last_issue_date, lang)}{h.last_issue_date ? ` ${h.last_issue_date.slice(0, 4)}` : ""}</dd>
      {h.age_hours !== undefined && h.age_hours !== null && <><dt>{t("h_age")}</dt><dd>{Math.round(h.age_hours)}</dd></>}
      {h.stale_stations && h.stale_stations.length > 0 && (
        <><dt>{t("h_stale_st")}</dt><dd>{h.stale_stations.map(stName).join(", ")}</dd></>
      )}
      <dt>{t("h_fresh")}</dt><dd><GateMark ok={h.forecast_fresh} /> {h.forecast_fresh ? t("yes") : t("no")}</dd>
      <dt>{t("h_degraded")}</dt><dd>{h.degraded_forecasts}</dd>
      <dt>{t("h_models")}</dt>
      <dd>{Object.entries(h.models).map(([k, v]) => (
        <span key={k} className="gate"><GateMark ok={v} />{models[k] ?? k}</span>
      ))}</dd>
      <dt>{t("h_version")}</dt><dd>{h.version}</dd>
    </dl>
  );
}
