// Типизированный клиент REST API AralShield (раздел 24.2).
// Новые поля помечены необязательными: панель работает и со старыми, и с новыми ответами API
// (контракты K1–K10 в reports/qa/judgement.md).

export type Level = "green" | "yellow" | "orange" | "red";
export type Lang = "ru" | "kk";
export type Mode = "live" | "backtest";

export interface Station {
  id: string; wmo: string; name_ru: string; name_kk: string; lat: number; lon: number;
  district_ru: string; district_kk: string;
}

export interface Settlement {
  id: string; name_ru: string; name_kk: string; lat: number; lon: number; population: number;
  forecast_station: string | null; has_station: boolean;
}

export interface Horizon {
  id: number; day: string; horizon: number; probability: number; level: Level;
  levels_by_group: Record<string, Level>; drivers: string[]; source_sector: string | null;
  source_km: number | null; wind_max: number | null; gust_max: number | null;
  wind_dir_sector: string | null; dry_days: number | null; cams_dust: number | null; degraded: boolean;
  /** K5: день прогноза уже прошёл (выпуск устарел). */
  past?: boolean;
  /** K2: коды причин сниженной уверенности. */
  degraded_reasons?: string[];
}

interface Place { id: string; name_kk: string; name_ru: string; lat?: number; lon?: number }

export interface StationForecast {
  settlement: Place & { lat: number; lon: number };
  /** K5: станция, по которой считается прогноз (id или объект). */
  forecast_station?: string | Place | null;
  issue_date?: string;
  issued_at: string; model_version: string; horizons: Horizon[];
  official_warning_url: string; disclaimer: string;
  /** K5: issue_date раньше сегодняшнего дня (Кызылорда). */
  stale?: boolean;
  age_hours?: number;
  mode?: Mode;
}

export interface Driver {
  feature: string; contribution: number; label_ru: string; label_kk: string; direction: "up" | "down";
}

export interface Explanation {
  id: number; station: string; day: string; probability: number; drivers: Driver[];
  source_sector: string | null; source_km: number | null; model_version: string; degraded: boolean;
  degraded_reasons?: string[];
  issued_at?: string;
  inputs: { notes?: string[]; fetched_at?: string; weather?: string; degraded_reasons?: string[];
    issued_at?: string; sector_map?: string };
}

export interface Accuracy {
  period: string; n: number; note?: string; from?: string; to?: string; events?: number;
  brier?: number | null; pr_auc?: number | null; orange_alerts?: number; orange_confirmed?: number;
  POD?: number | null; FAR?: number | null; text_ru?: string; text_kk?: string;
  horizon?: number; source?: string; mode?: Mode | string; as_of?: string;
}

export interface CalendarData {
  mode?: Mode;
  events: { date: string; dust: number | null; strong: number; reliable: number }[];
  forecasts_h1: { day: string; probability: number; level: Level }[];
}

export interface ModelSummary {
  n: number; events: number; pr_auc: number; roc_auc: number; brier: number; bss: number; ece: number;
  pr_auc_ci?: [number, number]; bss_ci?: [number, number];
}

export interface Contingency { threshold: number; POD: number; FAR: number; CSI: number; TP: number; FP: number; FN: number }

export interface HorizonReport {
  models: Record<string, ModelSummary>;
  split: Record<string, [number, number, number, number]>;
  rule_B2: { gust_ms: number; dry_days: number };
  shap_top: Record<string, number>;
  per_station: Record<string, { events: number; M2_pr_auc: number; B2_pr_auc: number; M2_bss: number }>;
  levels?: Record<string, { yellow: number; orange: number; red: number }>;
  at_levels?: Record<string, Contingency>;
  binary?: unknown;
  /** K10: "era5_actual_upper_bound" для h1/h2. */
  weather_inputs?: string;
}

export interface H4Cell {
  n: number; odds_ratio: number; p_value?: number; "P(kyzylorda)": number; "P(kyzylorda|aralsk)": number;
  /** A16: ОШ Мантеля–Хензеля (страты год–месяц) и p перестановки недельных блоков. */
  mh_or?: number; p_perm?: number;
}

export type GateValue = boolean | string | number | number[] | null;

export interface ForecastReport {
  h0: HorizonReport; h1: HorizonReport; h2: HorizonReport;
  gates: Record<string, Record<string, GateValue>>;
  H4: { conclusion?: string; conclusion_ru?: string; conclusion_kk?: string }
    & Record<string, { all?: H4Cell; nw_w_wind?: H4Cell; significant?: boolean } | string | undefined>;
  B3_cams: { available: boolean; error?: string; period?: string; cams?: ModelSummary; model?: ModelSummary;
    note?: string; note_ru?: string; note_kk?: string };
  operational?: { available: boolean; error?: string; period?: string; reanalysis?: ModelSummary;
    operational?: ModelSummary; rule_B2_operational?: ModelSummary; note?: string; note_ru?: string; note_kk?: string;
    excluded_stations?: string[]; identical_frac?: Record<string, number> };
  replay?: {
    event_date: string; stations_with_dust: number;
    series: Record<string, { h0?: SeriesPoint[]; h1?: SeriesPoint[]; h2?: SeriesPoint[];
      drivers_h1?: { feature: string; contribution: number }[] }>;
  };
  model_version: string; created: string;
}
export interface SeriesPoint { date: string; p: number; y: number }

export interface EyeReport {
  tag: string;
  cv: { overall_accuracy: number; oa_ci95: [number, number]; kappa: number; f1: Record<string, number>;
    iou: Record<string, number>; n_samples: number; validation: string; class_counts: Record<string, number>;
    meaning?: string; meaning_ru?: string; meaning_kk?: string; informative?: boolean };
  external_worldcover?: { n_pixels: number; overall_agreement: number | null; kappa: number | null;
    recall: Record<"water" | "veg" | "bare", number | null>; note: string; note_ru?: string; note_kk?: string;
    independent?: boolean; baseline_majority?: number | null };
  area_km2: Record<string, number>;
  sector_source: Record<string, number[]>;
  /** B2: доля сектора, покрытая композитом (16 значений на станцию), и среднее по станции. */
  sector_coverage?: Record<string, number[]>;
  sector_coverage_mean?: Record<string, number>;
  feature_importance: Record<string, number>;
  labels: string;
}

export interface Layers {
  period: string;
  tag?: string;
  classes: Record<string, { ru: string; kk: string; color: string }>;
  layers: Record<string, { url: string; bounds: [[number, number], [number, number]] }>;
}

export interface Distribution { mean: number; p5: number; p95: number }

export interface ShieldReport {
  cells: number; cell_km: number; class_counts: Record<string, number>;
  sensitivity: { scenarios: number; top_n: number; kendall_tau_top_mean: number; kendall_tau_top_min: number;
    top_overlap_mean?: number; top_overlap_min?: number; rbo_mean?: number; robust_cells: number };
  comparison_1000ha: { area_ha: number; by_rating: ScenarioResult; random_suitable_people_weighted_mean?: number };
  wind_rose_8ms: Record<string, number>;
  gate_rank_stability_ge_0_7: boolean;
  top_overlap_mean?: number;
}

export type Plan = "rating" | "random" | "nearest" | "custom";

export interface ScenarioResult {
  cells: number; area_ha: number; total_reduction_pct: number; people_weighted_reduction: number;
  per_settlement: { settlement: string; name_ru: string; name_kk: string; population: number;
    load_before: number; load_after: number; reduction_pct: number }[];
  cell_ids?: number[];
  plan?: Plan;
  /** K7: для случайного плана — распределение по розыгрышам. */
  distribution?: Distribution;
  people_weighted_reduction_dist?: Distribution;
}

export interface PriorityProps {
  cell_id: number; priority: number; class: string; suitability: number; source: number; stability: number;
  protects: string; protected_population: number; area_ha: number;
}

export type Priorities = GeoJSON.FeatureCollection<GeoJSON.Polygon, PriorityProps> & { total_matching?: number; returned?: number };

export interface Health {
  status: "ok" | "stale" | "degraded" | string; version: string; last_issue_date: string | null;
  last_created_at?: string | null; last_issued_at?: string | null; forecast_fresh: boolean; stale?: boolean;
  age_hours?: number | null; stale_stations?: string[]; issued_today?: boolean;
  degraded_forecasts: number; models: Record<string, boolean>;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(url: string, init?: RequestInit, asText = false): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail ?? msg; } catch { /* не JSON */ }
    throw new ApiError(r.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return (asText ? r.text() : r.json()) as Promise<T>;
}

const qs = (p: Record<string, string | number | undefined>) =>
  new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString();

const sig = (signal?: AbortSignal): RequestInit | undefined => (signal ? { signal } : undefined);

export interface PreviewParams {
  settlement: string; horizon: number; role: string; lang: Lang; channel: string;
  mode?: Mode; issue_date?: string;
}

export const PRIORITY_LIMIT = 7000;

export const api = {
  stations: (signal?: AbortSignal) => request<Station[]>("/v1/stations", sig(signal)),
  settlements: (signal?: AbortSignal) => request<Settlement[]>("/v1/settlements", sig(signal)),
  forecasts: (signal?: AbortSignal) => request<StationForecast[]>("/v1/forecasts", sig(signal)),
  /** Прогноз одного пункта (в т.ч. архивный: mode=backtest&issue_date=…). */
  forecastFor: (p: { settlement: string; mode?: Mode; issue_date?: string }, signal?: AbortSignal) =>
    request<StationForecast>(`/v1/forecasts?${qs(p)}`, sig(signal)),
  explanation: (id: number, signal?: AbortSignal) => request<Explanation>(`/v1/forecasts/${id}/explanation`, sig(signal)),
  preview: (p: PreviewParams, signal?: AbortSignal) =>
    request<string>(`/v1/messages/preview?${qs({ ...p })}`, sig(signal), true),
  summary: (lang: Lang, signal?: AbortSignal) => request<string>(`/v1/summary?lang=${lang}`, sig(signal), true),
  accuracy: (p: { settlement?: string; period: string; horizon?: number }, signal?: AbortSignal) =>
    request<Accuracy>(`/v1/accuracy?${qs(p)}`, sig(signal)),
  calendar: (station: string, year: number, signal?: AbortSignal) =>
    request<CalendarData>(`/v1/events/calendar?${qs({ station, year })}`, sig(signal)),
  forecastReport: (signal?: AbortSignal) => request<ForecastReport>("/v1/reports/forecast", sig(signal)),
  // Метку карты «Глаза» не передаём: API берёт config.EYE_TAG (контракт K1).
  eyeReport: (signal?: AbortSignal) => request<EyeReport>("/v1/reports/eye", sig(signal)),
  shieldReport: (signal?: AbortSignal) => request<ShieldReport>("/v1/reports/shield", sig(signal)),
  layers: (signal?: AbortSignal) => request<Layers>("/v1/sources/preview", sig(signal)),
  priorities: (minSuit: number, signal?: AbortSignal) =>
    request<Priorities>(
      `/v1/priorities?${qs({ min_suitability: minSuit, classes: "надёжный высокий,высокий,средний", limit: PRIORITY_LIMIT })}`,
      sig(signal)),
  scenario: (body: { plan: Plan; cell_ids?: number[]; area_ha?: number }) =>
    request<ScenarioResult>("/v1/scenarios", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }),
  exportUrl: (fmt: string) => `/v1/priorities/export?fmt=${fmt}`,
  health: (signal?: AbortSignal) => request<Health>("/v1/health", sig(signal)),
};

/** Событие для демо (контракт K8): дата из отчёта, станция с наибольшей p среди станций с пылью. */
export function demoEvent(r: ForecastReport | null | undefined): { event: string; issue: string; station: string } | null {
  const ev = r?.replay?.event_date;
  if (!ev || !r?.replay?.series) return null;
  let best: { st: string; p: number } | null = null;
  for (const [st, s] of Object.entries(r.replay.series)) {
    const pt = (s.h1 ?? []).find((x) => x.date === ev);
    if (pt && pt.y === 1 && (!best || pt.p > best.p)) best = { st, p: pt.p };
  }
  if (!best) return null;
  const d = new Date(`${ev}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return { event: ev, issue: d.toISOString().slice(0, 10), station: best.st };
}
