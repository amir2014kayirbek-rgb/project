import { useEffect, useState } from "react";

export interface Async<T> { data: T | null; error: string | null; loading: boolean }

/**
 * Загружает данные при изменении зависимостей. При смене зависимостей старые данные сбрасываются
 * (чтобы не показывать сообщение другого пункта под новым заголовком), устаревший запрос отменяется.
 */
export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[]): Async<T> {
  const [state, setState] = useState<Async<T>>({ data: null, error: null, loading: true });
  useEffect(() => {
    const ctl = new AbortController();
    let alive = true;
    setState({ data: null, error: null, loading: true });
    fn(ctl.signal).then(
      (data) => alive && setState({ data, error: null, loading: false }),
      (e: unknown) => {
        if (!alive || (e instanceof DOMException && e.name === "AbortError")) return;
        setState({ data: null, error: e instanceof Error ? e.message : String(e), loading: false });
      },
    );
    return () => { alive = false; ctl.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

/** Значение, которое обновляется только после паузы `ms` (для ползунков, чтобы не слать запрос на каждый шаг). */
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}
