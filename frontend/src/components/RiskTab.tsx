import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import { CircleMarker, Marker, Tooltip } from "react-leaflet";
import { api, type Horizon, type Lang, type Level, type StationForecast } from "../api";
import { useAsync } from "../hooks";
import {
  HIDDEN_DRIVERS, LEVEL_ICON, LEVEL_TEXT, LEVELS, REASON_TEXT, ROLES, SECTOR_TEXT, addDays, featureLabel, fmtDate, fmtInt,
  fmtPct, localToday, useT,
} from "../i18n";
import { BaseMap, LEVEL_COLOR, LevelBadge, Loading, Seg, levelLabel } from "./common";

const issueDate = (f: StationForecast) => f.issue_date ?? f.issued_at.slice(0, 10);
const isPast = (hz: Horizon, today: string) => hz.past ?? hz.day < today;
const isStale = (f: StationForecast, today: string) => f.stale ?? issueDate(f) < today;
const ageHours = (f: StationForecast) =>
  f.age_hours ?? Math.max(0, (Date.now() - new Date(f.issued_at).getTime()) / 3.6e6);
const reasonsOf = (hz?: Horizon, extra?: string[]) => [...new Set([...(hz?.degraded_reasons ?? []), ...(extra ?? [])])];

/** Подпись срока по фактической дате: «Сегодня/Завтра/Послезавтра» или дата для устаревшего выпуска. */
function useDayLabel() {
  const { t, lang } = useT();
  const today = localToday();
  return (day: string) => {
    if (day === today) return t("today");
    if (day === addDays(today, 1)) return t("tomorrow");
    if (day === addDays(today, 2)) return t("after");
    return fmtDate(day, lang);
  };
}

const pinCache = new Map<string, L.DivIcon>();
function pin(level: Level | null) {
  const k = level ?? "none";
  if (!pinCache.has(k)) {
    pinCache.set(k, L.divIcon({
      className: "",
      html: `<span class="pin ${level ? `lv-${level}` : "lv-none"}" aria-hidden="true">${level ? LEVEL_ICON[level] : "?"}</span>`,
      iconSize: [18, 18], iconAnchor: [9, 9],
    }));
  }
  return pinCache.get(k)!;
}

export default function RiskTab() {
  const { t, lang } = useT();
  const fc = useAsync((s) => api.forecasts(s), []);
  const settlements = useAsync((s) => api.settlements(s), []);
  const [sel, setSel] = useState<string | null>(null);
  const [h, setH] = useState<number | null>(null);
  const list = fc.data ?? [];
  const today = localToday();
  const dayLabel = useDayLabel();
  const ref = list[0];

  useEffect(() => {
    if (!sel && list.length) setSel(list[0].settlement.id);
  }, [list, sel]);
  // По умолчанию — срок, который приходится на завтрашний день; при устаревшем выпуске — первый непрошедший.
  useEffect(() => {
    if (h !== null || !ref) return;
    const tomorrow = ref.horizons.find((x) => x.day === addDays(today, 1));
    const firstFuture = ref.horizons.find((x) => !isPast(x, today));
    setH(tomorrow?.horizon ?? firstFuture?.horizon ?? 1);
  }, [ref, h, today]);
  const hh = h ?? 1;

  const name = (s: { name_ru: string; name_kk: string }) => (lang === "kk" ? s.name_kk : s.name_ru);
  const selected = list.find((f) => f.settlement.id === sel) ?? null;
  const staleF = list.find((f) => isStale(f, today));

  return (
    <>
      {staleF && (
        <div className="banner warn" role="status">
          <b>⚠ {t("stale_title")}.</b>{" "}
          {t("stale_body", { date: fmtDate(issueDate(staleF), lang), age: Math.round(ageHours(staleF)) })}
        </div>
      )}
      <div className="split">
        <div className="mapwrap">
          <BaseMap>
            {(settlements.data ?? []).filter((s) => !s.has_station).map((s) => {
              const f = list.find((x) => x.settlement.id === s.forecast_station);
              const hz = f?.horizons.find((x) => x.horizon === hh);
              return (
                <Marker key={s.id} position={[s.lat, s.lon]} icon={pin(hz?.level ?? null)} keyboard={false}>
                  <Tooltip>{name(s)} · {t("population")} ~{fmtInt(s.population)}<br />
                    {hz ? `${levelLabel(hz.level, lang)} · ${fmtPct(hz.probability)}` : "—"}<br />
                    ({t("by_station", { st: f ? name(f.settlement) : "—" })})</Tooltip>
                </Marker>
              );
            })}
            {list.map((f) => {
              const hz = f.horizons.find((x) => x.horizon === hh);
              if (!hz) return null;
              return (
                <CircleMarker key={f.settlement.id} center={[f.settlement.lat, f.settlement.lon]}
                  radius={10 + hz.probability * 18}
                  eventHandlers={{ click: () => setSel(f.settlement.id) }}
                  pathOptions={{ color: "#1f1c18", weight: f.settlement.id === sel ? 3 : 1.5,
                    fillColor: LEVEL_COLOR[hz.level], fillOpacity: 0.85, dashArray: hz.degraded ? "4 3" : undefined }}>
                  <Tooltip permanent direction="right" offset={[12, 0]}>
                    <b>{name(f.settlement)}</b> <span aria-hidden="true">{LEVEL_ICON[hz.level]}</span> {fmtPct(hz.probability)}
                    <span className="lvw"> · {LEVEL_TEXT[hz.level][lang]}</span>
                  </Tooltip>
                </CircleMarker>
              );
            })}
          </BaseMap>
          {ref && (
            <div className="horizon">
              <Seg label={t("horizon")} value={hh} onChange={setH}
                options={ref.horizons.map((x) => ({
                  value: x.horizon,
                  label: isPast(x, today) ? <>{dayLabel(x.day)} <small>({t("past")})</small></> : dayLabel(x.day),
                  title: fmtDate(x.day, lang, true),
                }))} />
            </div>
          )}
        </div>
        <aside className="panel" id="side" tabIndex={-1}>
          <div className="row between">
            <h2>{t("risk_title")}</h2>
            {ref && <span className="muted small">{t("issued")}: {fmtDate(issueDate(ref), lang)}</span>}
          </div>
          <Loading error={fc.error} loading={fc.loading} />
          {!fc.loading && !fc.error && list.length === 0 && <p className="muted">{t("no_forecast")}</p>}
          <div className="cards">
            {list.map((f) => <StationCard key={f.settlement.id} f={f} h={hh} active={f.settlement.id === sel}
              onClick={() => setSel(f.settlement.id)} />)}
          </div>
          <div className="legend">
            {LEVELS.map((l) => <LevelBadge key={l} level={l} />)}
          </div>
          {selected && <Detail key={`${selected.settlement.id}-${hh}`} f={selected} h={hh} />}
          <p className="disclaimer">{t("disclaimer")}</p>
        </aside>
      </div>
    </>
  );
}

function StationCard({ f, h, active, onClick }: { f: StationForecast; h: number; active: boolean; onClick: () => void }) {
  const { t, lang } = useT();
  const today = localToday();
  const hz = f.horizons.find((x) => x.horizon === h);
  return (
    <button className="fc" aria-pressed={active} onClick={onClick}>
      <span className="name">{lang === "kk" ? f.settlement.name_kk : f.settlement.name_ru}</span>
      <span className="p">{hz ? fmtPct(hz.probability) : "—"}</span>
      <span>{hz && <LevelBadge level={hz.level} />}{hz?.degraded && <span className="small err"> ⚠</span>}</span>
      <span />
      <span className="bars">
        {f.horizons.map((x) => (
          <span key={x.horizon} className={`bcell${isPast(x, today) ? " past" : ""}`}>
            <span className="bl">
              {fmtDate(x.day, lang).replace(/^(\d+ \S{3})\S*/, "$1")}{" "}
              <span aria-hidden="true">{LEVEL_ICON[x.level]}</span> {fmtPct(x.probability)}
              {isPast(x, today) && <span className="sr"> ({t("past")})</span>}
            </span>
            <span className="bar" aria-hidden="true">
              <i style={{ width: `${Math.max(4, x.probability * 100)}%`, background: LEVEL_COLOR[x.level] }} />
            </span>
          </span>
        ))}
      </span>
    </button>
  );
}

function reasonText(code: string, lang: Lang) {
  return REASON_TEXT[code]?.[lang] ?? code;
}

function Detail({ f, h }: { f: StationForecast; h: number }) {
  const { t, lang } = useT();
  const hz = f.horizons.find((x) => x.horizon === h);
  const exp = useAsync((s) => (hz ? api.explanation(hz.id, s) : Promise.resolve(null)), [hz?.id]);
  const [role, setRole] = useState("resident");
  const msg = useAsync((s) => api.preview({ settlement: f.settlement.id, horizon: h, role, lang, channel: "telegram" }, s),
    [f.settlement.id, h, role, lang]);
  const drivers = useMemo(
    () => (exp.data?.drivers ?? []).filter((d) => !HIDDEN_DRIVERS.has(d.feature) && Number.isFinite(d.contribution)),
    [exp.data]);
  const maxAbs = useMemo(() => Math.max(0.01, ...drivers.map((d) => Math.abs(d.contribution))), [drivers]);
  if (!hz) return null;
  const reasons = reasonsOf(hz, exp.data?.degraded_reasons ?? exp.data?.inputs?.degraded_reasons);
  const notes = exp.data?.inputs?.notes ?? [];
  const sector = (s: string) => SECTOR_TEXT[s]?.[lang] ?? s;
  return (
    <div className="detail">
      <h3>{lang === "kk" ? f.settlement.name_kk : f.settlement.name_ru} · {fmtDate(hz.day, lang, true)}</h3>
      {(hz.degraded || reasons.length > 0) && (
        <div className="err small">
          <p>⚠ {t("degraded")}</p>
          {reasons.length > 0 && (
            <ul aria-label={t("degraded_why")}>{reasons.map((r) => <li key={r}>{reasonText(r, lang)}</li>)}</ul>
          )}
        </div>
      )}
      <dl className="kv">
        <dt>{t("wind")}</dt>
        <dd>{hz.wind_max ?? "—"} {t("ms")}{hz.wind_dir_sector ? ` (${sector(hz.wind_dir_sector)})` : ""},
          {" "}{t("gust")} {hz.gust_max ?? "—"} {t("ms")}</dd>
        <dt>{t("dry")}</dt><dd>{hz.dry_days ?? "—"}</dd>
        <dt>{t("source")}</dt>
        <dd>{hz.source_sector ? `${sector(hz.source_sector)}${hz.source_km ? `, ~${Math.round(hz.source_km)} ${t("km")}` : ""}` : "—"}</dd>
        <dt>{t("cams")}</dt><dd>{hz.cams_dust !== null ? `${hz.cams_dust} мкг/м³` : "—"}</dd>
      </dl>
      {exp.loading && <p className="muted small">{t("loading")}</p>}
      {drivers.length > 0 && (
        <>
          <h3>{t("why")}</h3>
          <ul className="drivers">
            {drivers.map((d) => (
              <li key={d.feature}>
                <span>{d.direction === "up" ? "↑" : "↓"} {featureLabel(d.feature, lang, d)}</span>
                <span className="bar"><i className={d.direction} style={{ width: `${(Math.abs(d.contribution) / maxAbs) * 100}%` }} /></span>
              </li>
            ))}
          </ul>
        </>
      )}
      {/* Примечания API пока только на русском; в казахском режиме показываем локализованные причины. */}
      {notes.length > 0 && (lang === "ru" || reasons.length === 0) && (
        <ul className="small muted" lang="ru">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
      )}
      <div className="row between">
        <h3>{t("message")}</h3>
        <label className="small">{t("role")}{" "}
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map((r) => <option key={r.id} value={r.id}>{lang === "kk" ? r.kk : r.ru}</option>)}
          </select>
        </label>
      </div>
      <pre className="msg tg" aria-busy={msg.loading}>{msg.loading ? t("loading") : msg.data ?? msg.error ?? "…"}</pre>
    </div>
  );
}
