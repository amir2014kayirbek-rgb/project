import { useEffect, useState } from "react";
import L from "leaflet";
import { ImageOverlay, useMap } from "react-leaflet";
import { Chart as ChartJS, Filler, LineElement, PointElement, RadialLinearScale, Tooltip } from "chart.js";
import { Radar } from "react-chartjs-2";
import { api, type EyeReport, type Layers } from "../api";
import { useAsync } from "../hooks";
import { SECTOR_ABBR, fmtInt, fmtNum, useT } from "../i18n";
import { BaseMap, Kpi, Loading, Seg } from "./common";

ChartJS.register(RadialLinearScale, PointElement, LineElement, Filler, Tooltip);
const SOURCE_CLASS = "Потенциальный источник";

/**
 * Обрезает слой модели по вертикальной линии шторки (в пикселях карты).
 * Применяется при появлении/загрузке слоя, смене слоя, движении, масштабе и изменении размера карты,
 * поэтому положение ползунка и обрезка всегда совпадают.
 */
function SwipeClip({ overlay, pct }: { overlay: L.ImageOverlay | null; pct: number }) {
  const map = useMap();
  useEffect(() => {
    if (!overlay) return;
    const apply = () => {
      const el = overlay.getElement();
      if (!el || !map.hasLayer(overlay)) return;
      const x = map.getSize().x * (pct / 100);
      const layerX = map.containerPointToLayerPoint([x, 0]).x;
      const imgX = L.DomUtil.getPosition(el)?.x ?? 0;
      el.style.clipPath = `inset(0 0 0 ${Math.max(0, layerX - imgX)}px)`;
    };
    apply();
    const raf = requestAnimationFrame(apply); // позиция картинки задаётся Leaflet после добавления в карту
    overlay.on("load add", apply);
    map.on("move zoom zoomend viewreset resize", apply);
    return () => {
      cancelAnimationFrame(raf);
      overlay.off("load add", apply);
      map.off("move zoom zoomend viewreset resize", apply);
    };
  }, [map, overlay, pct]);
  return null;
}

export default function SourcesTab() {
  const { t, lang } = useT();
  const layers = useAsync((s) => api.layers(s), []);
  const eye = useAsync((s) => api.eyeReport(s), []);
  const stations = useAsync((s) => api.stations(s), []);
  const [layer, setLayer] = useState<"classes" | "source_prob">("classes");
  const [pct, setPct] = useState(50);
  const [station, setStation] = useState("aralsk");
  const [overlay, setOverlay] = useState<L.ImageOverlay | null>(null);
  const L_ = layers.data as Layers | null;
  const classKk = (ru: string) => Object.values(L_?.classes ?? {}).find((c) => c.ru === ru)?.kk ?? ru;
  const clsName = (ru: string) => (lang === "kk" ? classKk(ru) : ru);

  return (
    <div className="split">
      <div className="mapwrap">
        <BaseMap center={[45.7, 60.2]} zoom={7}>
          {L_?.layers.rgb && <ImageOverlay url={L_.layers.rgb.url} bounds={L_.layers.rgb.bounds} opacity={1} />}
          {L_?.layers[layer] && (
            <ImageOverlay key={layer} ref={setOverlay} url={L_.layers[layer].url} bounds={L_.layers[layer].bounds} opacity={0.9} />
          )}
          <SwipeClip overlay={overlay} pct={pct} />
        </BaseMap>
        {L_?.layers[layer] && <div className="swipe-line" style={{ left: `${pct}%` }} aria-hidden="true" />}
        <div className="swipe">
          <label>{t("swipe")}
            <input type="range" min={0} max={100} value={pct} onChange={(e) => setPct(Number(e.target.value))}
              aria-valuetext={`${pct}%`} />
          </label>
        </div>
      </div>
      <aside className="panel" id="side" tabIndex={-1}>
        <h2>{t("eye_title")}</h2>
        <Loading error={layers.error ?? eye.error} loading={layers.loading} />
        {L_ && (
          <div className="legend col">
            {Object.entries(L_.classes).map(([k, c]) => (
              <span key={k}><i className="sw" style={{ background: c.color }} />{lang === "kk" ? c.kk : c.ru}
                {eye.data?.area_km2 ? ` — ${fmtInt(eye.data.area_km2[c.ru] ?? 0)} км²` : ""}</span>
            ))}
          </div>
        )}
        <Seg label={t("layer")} value={layer} onChange={setLayer}
          options={[{ value: "classes", label: t("classes") }, { value: "source_prob", label: t("source_prob") }]} />
        {eye.data && <EyeMetrics e={eye.data} clsName={clsName} />}
        {eye.data && (
          <>
            <h3>{t("sector_rose")}</h3>
            <label className="small">{t("station")}{" "}
              <select value={station} onChange={(e) => setStation(e.target.value)}>
                {(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{lang === "kk" ? s.name_kk : s.name_ru}</option>)}
              </select>
            </label>
            <Radar data={{ labels: SECTOR_ABBR[lang], datasets: [{ label: "F(θ)", data: eye.data.sector_source[station] ?? [],
              borderColor: "#c0392b", backgroundColor: "rgba(192,57,43,.2)", fill: true }] }}
              options={{ locale: "en-US", plugins: { legend: { display: false } }, scales: { r: { beginAtZero: true } } }} />
            <p className="muted small">{t("sector_note")}</p>
            {(eye.data.sector_coverage_mean?.[station] ?? 1) < 0.5 && (
              <p className="err small">⚠ {t("sector_lowcov", { pct: Math.round((eye.data.sector_coverage_mean?.[station] ?? 0) * 100) })}</p>
            )}
          </>
        )}
      </aside>
    </div>
  );
}

function EyeMetrics({ e, clsName }: { e: EyeReport; clsName: (ru: string) => string }) {
  const { t, lang } = useT();
  const wc = e.external_worldcover;
  const cv = e.cv;
  const wcNote = wc ? (lang === "kk" ? wc.note_kk : wc.note_ru) : undefined;
  // Старые отчёты содержат только русский текст meaning; новые — meaning_ru/meaning_kk (B8).
  const meaning = lang === "kk" ? cv.meaning_kk ?? (cv.meaning ? t("weak_meaning") : undefined) : cv.meaning_ru ?? cv.meaning;
  const informative = cv.informative !== false;
  return (
    <>
      {wc && (
        <>
          {/* Разметка воды и растительности сама взята из WorldCover, поэтому это не независимая проверка (B1). */}
          <h3>{t("wc_title")}</h3>
          <div className="kpis">
            <Kpi value={fmtNum(wc.overall_agreement)} label={t("agreement")} />
            {wc.baseline_majority !== undefined && wc.baseline_majority !== null && (
              <Kpi value={fmtNum(wc.baseline_majority)} label={t("baseline_major")} />
            )}
            <Kpi value={fmtNum(wc.kappa)} label="κ" />
            <Kpi value={fmtNum(wc.recall.water)} label={t("water")} />
            <Kpi value={fmtNum(wc.recall.bare)} label={t("bare")} />
            <Kpi value={fmtNum(wc.recall.veg)} label={t("veg")} />
          </div>
          <p className="muted small">{wcNote ?? t("wc_note")} {fmtInt(wc.n_pixels)} px.</p>
        </>
      )}
      <h3>{t("weak_title")}</h3>
      {informative && (
        <div className="kpis">
          <Kpi value={fmtNum(cv.overall_accuracy)} label={`OA (${fmtNum(cv.oa_ci95[0])}–${fmtNum(cv.oa_ci95[1])})`} />
          <Kpi value={fmtNum(cv.kappa)} label="κ" />
          <Kpi value={fmtNum(cv.f1[SOURCE_CLASS])} label={t("f1_source")} />
        </div>
      )}
      {!informative && <p className="err small">⚠ {t("weak_uninformative")}</p>}
      {meaning && <p className="err small">⚠ {meaning}</p>}
      <div className="t-scroll">
        <table className="t" style={{ marginTop: 10 }}>
          <thead><tr><th>{t("classes")}</th><th>F1</th><th>IoU</th><th>n</th></tr></thead>
          <tbody>{Object.keys(cv.f1).map((k) => (
            <tr key={k}><td>{clsName(k)}</td><td>{fmtNum(cv.f1[k])}</td><td>{fmtNum(cv.iou[k])}</td>
              <td>{fmtInt(cv.class_counts[k])}</td></tr>))}</tbody>
        </table>
      </div>
      <p className="muted small">{t("cv_note")}{lang === "ru" ? ` ${e.labels}.` : ""}</p>
    </>
  );
}
