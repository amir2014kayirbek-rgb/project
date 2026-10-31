import { lazy, Suspense, useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { Lang } from "./api";
import { LangContext, type Key, useT } from "./i18n";
import RiskTab from "./components/RiskTab";

const ArchiveTab = lazy(() => import("./components/ArchiveTab"));
const SourcesTab = lazy(() => import("./components/SourcesTab"));
const ShieldTab = lazy(() => import("./components/ShieldTab"));
const ReportsTab = lazy(() => import("./components/ReportsTab"));

type Tab = "risk" | "archive" | "sources" | "shield" | "reports";
const TABS: { id: Tab; key: Key }[] = [
  { id: "risk", key: "tab_risk" }, { id: "archive", key: "tab_archive" }, { id: "sources", key: "tab_sources" },
  { id: "shield", key: "tab_shield" }, { id: "reports", key: "tab_reports" },
];

function readHash(): Tab {
  const h = window.location.hash.slice(1) as Tab;
  return TABS.some((t) => t.id === h) ? h : "risk";
}

function initialLang(): Lang {
  try {
    const v = localStorage.getItem("aralshield.lang");
    if (v === "ru" || v === "kk") return v;
  } catch { /* хранилище недоступно */ }
  return "ru";
}

export default function App() {
  const [lang, setLang] = useState<Lang>(initialLang);
  const [tab, setTab] = useState<Tab>(readHash);

  useEffect(() => {
    const on = () => setTab(readHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
    try { localStorage.setItem("aralshield.lang", lang); } catch { /* ignore */ }
  }, [lang]);

  return (
    <LangContext.Provider value={lang}>
      <Header tab={tab} lang={lang} setLang={setLang} />
      <main id="panel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
        <Suspense fallback={<p className="muted">…</p>}>
          {tab === "risk" && <RiskTab />}
          {tab === "archive" && <ArchiveTab />}
          {tab === "sources" && <SourcesTab />}
          {tab === "shield" && <ShieldTab />}
          {tab === "reports" && <ReportsTab />}
        </Suspense>
      </main>
      <Footer />
    </LangContext.Provider>
  );
}

function Header({ tab, lang, setLang }: { tab: Tab; lang: Lang; setLang: (l: Lang) => void }) {
  const { t } = useT();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const go = (id: Tab) => { window.location.hash = id; refs.current[id]?.focus(); };
  // Шаблон ARIA «вкладки»: стрелки влево/вправо, Home/End; в порядке табуляции только активная вкладка.
  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    const i = TABS.findIndex((x) => x.id === tab);
    const n = TABS.length;
    const j = e.key === "ArrowRight" ? (i + 1) % n : e.key === "ArrowLeft" ? (i - 1 + n) % n
      : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : -1;
    if (j < 0) return;
    e.preventDefault();
    go(TABS[j].id);
  };
  return (
    <header className="top">
      <div className="brand">
        <svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 2 4 7v8c0 7 5 12 12 15 7-3 12-8 12-15V7z" /></svg>
        <div><strong>AralShield</strong> <span className="muted">«Тұз дауылы»</span>
          <div className="sub">{t("tagline")}</div></div>
      </div>
      <nav className="tabs" role="tablist" aria-label={t("tabs_label")} onKeyDown={onKey}>
        {TABS.map((x) => (
          <button key={x.id} id={`tab-${x.id}`} role="tab" aria-selected={tab === x.id} aria-controls="panel"
            tabIndex={tab === x.id ? 0 : -1} ref={(el) => { refs.current[x.id] = el; }}
            onClick={() => go(x.id)}>{t(x.key)}</button>
        ))}
      </nav>
      <div className="lang" role="group" aria-label={t("lang_label")}>
        <button aria-pressed={lang === "kk"} lang="kk" onClick={() => setLang("kk")}>ҚАЗ</button>
        <button aria-pressed={lang === "ru"} lang="ru" onClick={() => setLang("ru")}>РУС</button>
      </div>
    </header>
  );
}

function Footer() {
  const { t } = useT();
  return (
    <footer className="foot muted small">
      AralShield v1 · {t("data_credits")} · <a href="/docs">API</a>
    </footer>
  );
}
