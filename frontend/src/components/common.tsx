import type { ReactNode } from "react";
import { MapContainer, TileLayer, ZoomControl } from "react-leaflet";
import type { Level } from "../api";
import { LEVEL_ICON, LEVEL_TEXT, useT } from "../i18n";

/** Заливка уровней на карте и в графиках; у значков текст контрастен фону не менее 4,5:1 (styles.css). */
export const LEVEL_COLOR: Record<Level, string> = {
  green: "#23703c", yellow: "#c9a400", orange: "#e07a1f", red: "#c0392b",
};
/** Цвет текста/значка поверх заливки уровня. */
export const LEVEL_INK: Record<Level, string> = {
  green: "#ffffff", yellow: "#1f1c18", orange: "#1f1c18", red: "#ffffff",
};

/** Уровень всегда дублируется словом и значком (раздел 15.2, WCAG). */
export function LevelBadge({ level }: { level: Level }) {
  const { lang } = useT();
  return (
    <span className={`badge lv-${level}`}>
      <span aria-hidden="true">{LEVEL_ICON[level]}</span>
      {LEVEL_TEXT[level][lang]}
    </span>
  );
}

/** Компактная метка «значок + слово» без заливки (подсказки на карте, легенды). */
export function levelLabel(level: Level, lang: "ru" | "kk") {
  return `${LEVEL_ICON[level]} ${LEVEL_TEXT[level][lang]}`;
}

/** Ссылка «Пропустить карту»: переводит фокус на боковую панель, не меняя #вкладку в адресе. */
export function SkipLink({ to = "side" }: { to?: string }) {
  const { t } = useT();
  return (
    <a className="skip" href={`#${to}`} onClick={(e) => {
      e.preventDefault();
      document.getElementById(to)?.focus();
    }}>{t("skip_map")}</a>
  );
}

export function BaseMap({ children, center = [45.7, 62.4], zoom = 7 }:
  { children?: ReactNode; center?: [number, number]; zoom?: number }) {
  const { t } = useT();
  return (
    <>
      <SkipLink />
      <MapContainer center={center} zoom={zoom} className="map" scrollWheelZoom preferCanvas zoomControl={false}
        attributionControl>
        {/* Нейтральная подложка, чтобы цвет риска был главным (раздел 25.3). Без интернета карта
            работает без подложки: слои проекта отдаются локальным сервером. */}
        <TileLayer
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
          attribution="Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors"
          maxZoom={14}
        />
        <ZoomControl key={t("zoom_in")} position="topleft" zoomInTitle={t("zoom_in")} zoomOutTitle={t("zoom_out")} />
        {children}
      </MapContainer>
    </>
  );
}

export function Loading({ error, loading }: { error: string | null; loading: boolean }) {
  const { t } = useT();
  if (error) return <p className="err" role="alert">{error}</p>;
  if (loading) return <p className="muted" aria-live="polite">{t("loading")}</p>;
  return null;
}

export function Kpi({ value, label }: { value: ReactNode; label: string }) {
  return <div className="kpi"><b>{value}</b><span>{label}</span></div>;
}

export function Seg<T extends string | number>({ value, options, onChange, label }:
  { value: T; options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[];
    onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.value)} aria-pressed={o.value === value} onClick={() => onChange(o.value)}
          disabled={o.disabled} title={o.title}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Чекбокс-индикатор ворот качества: значок + слово, не только цвет. */
export function GateMark({ ok }: { ok: boolean }) {
  const { t } = useT();
  return <span className={ok ? "ok" : "no"}>{ok ? "✓" : "✗"}<span className="sr">{ok ? t("yes") : t("no")}</span></span>;
}
