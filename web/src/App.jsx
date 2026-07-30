import { useEffect, useMemo, useState, useCallback } from "react";

const VERDICT = {
  strong: { label: "Strong Buy", glyph: "◆" },
  fair: { label: "Fair", glyph: "●" },
  skip: { label: "Skip", glyph: "·" },
};
const VERDICT_ORDER = { strong: 0, fair: 1, skip: 2 };

// verdict string (as written by config.VERDICT_*) -> slug used in CSS classes / filters
const VERDICT_SLUG = { "Strong Buy": "strong", Fair: "fair", Skip: "skip" };

// Local dev reads the synced copy in public/ (npm run dev syncs it from ../state).
// The deployed build fetches straight from GitHub's raw content each page load, so the
// site is always current the moment CI commits a new deals_history.json — no rebuild
// or redeploy needed when the data changes, only when the UI code itself changes.
const DATA_URL = import.meta.env.DEV
  ? "/data.json"
  : "https://raw.githubusercontent.com/josephararil/shopping-assistant/main/state/deals_history.json";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function fmt2(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return null;
  return n.toFixed(2);
}

function fmtEur(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return null;
  return Number.isInteger(n) ? `${n}` : n.toFixed(0);
}

function fmtPct(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return null;
  return `${Math.round(n * 100)}%`;
}

function unitSuffix(unit) {
  if (unit === "kg") return "/kg";
  if (unit === "L") return "/L";
  if (unit === "pc") return "/pc";
  return "";
}

function entryKey(e) {
  return `${e.sku || e.label || e.name}|${e.valid_until || ""}`;
}

function parseISO(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((s || "").trim());
  if (!m) return null;
  return { y: +m[1], mo: +m[2] - 1, d: +m[3] };
}

function fmtDate(s) {
  const p = parseISO(s);
  return p ? `${p.d} ${MONTHS[p.mo]} ${p.y}` : s || "";
}

function verdictSlug(v) {
  return VERDICT_SLUG[v] || null;
}

function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem("df-theme") || "system");
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    localStorage.setItem("df-theme", theme);
  }, [theme]);
  const toggle = useCallback(() => {
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    setTheme((t) => {
      const current = t === "system" ? (prefersDark ? "dark" : "light") : t;
      return current === "dark" ? "light" : "dark";
    });
  }, []);
  return [theme, toggle];
}

export default function App() {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);
  const [verdicts, setVerdicts] = useState({ strong: true, fair: true, skip: true });
  const [retailer, setRetailer] = useState("all");
  const [skuClass, setSkuClass] = useState("all");
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState("date");
  const [selectedKey, setSelectedKey] = useState(null);
  const [theme, toggleTheme] = useTheme();

  useEffect(() => {
    fetch(DATA_URL)
      .then((r) => r.json())
      .then((d) => setEntries(d.entries || []))
      .catch((e) => setError(String(e)));
  }, []);

  const retailers = useMemo(() => {
    if (!entries) return [];
    return [...new Set(entries.map((e) => e.retailer).filter(Boolean))].sort();
  }, [entries]);

  const stats = useMemo(() => {
    if (!entries || !entries.length) return null;
    const savings = entries.map((e) => e.saving_eur).filter((n) => n != null);
    return {
      total: entries.length,
      strong: entries.filter((e) => verdictSlug(e.verdict) === "strong").length,
      bestSaving: savings.length ? Math.max(...savings) : null,
      latest: entries.map((e) => e.valid_until).filter(Boolean).sort().pop(),
    };
  }, [entries]);

  const visible = useMemo(() => {
    if (!entries) return [];
    const q = query.trim().toLowerCase();
    let list = entries.filter((e) => {
      const slug = verdictSlug(e.verdict);
      if (slug && verdicts[slug] === false) return false;
      if (retailer !== "all" && e.retailer !== retailer) return false;
      if (skuClass !== "all" && e.sku_class !== skuClass) return false;
      return true;
    });
    if (q)
      list = list.filter((e) =>
        `${e.label || ""} ${e.name || ""} ${e.retailer || ""}`.toLowerCase().includes(q)
      );
    list = [...list].sort((a, b) => {
      const sa = verdictSlug(a.verdict);
      const sb = verdictSlug(b.verdict);
      if (sortBy === "date")
        return (
          (b.valid_until || "").localeCompare(a.valid_until || "") ||
          (VERDICT_ORDER[sa] ?? 3) - (VERDICT_ORDER[sb] ?? 3)
        );
      if (sortBy === "score") return (b.rank_score ?? -1) - (a.rank_score ?? -1);
      if (sortBy === "price")
        return (a.price_eur ?? Infinity) - (b.price_eur ?? Infinity);
      return 0;
    });
    return list;
  }, [entries, verdicts, retailer, skuClass, query, sortBy]);

  const selected = useMemo(
    () => entries?.find((e) => entryKey(e) === selectedKey) || null,
    [entries, selectedKey]
  );

  const counts = useMemo(() => {
    const c = { strong: 0, fair: 0, skip: 0 };
    (entries || []).forEach((e) => {
      const slug = verdictSlug(e.verdict);
      if (c[slug] != null) c[slug]++;
    });
    return c;
  }, [entries]);

  if (error)
    return (
      <div className="center-msg">
        <div className="center-card">
          <div className="center-glyph">⚠</div>
          Couldn’t load the deals feed.
          <code>{error}</code>
        </div>
      </div>
    );
  if (!entries)
    return (
      <div className="center-msg">
        <div className="loader" aria-label="Loading">
          <span className="gem-spin">◆</span>
          <p>Polishing the latest finds…</p>
        </div>
      </div>
    );

  return (
    <div className="app">
      <GradientDefs />
      <div className="aurora" aria-hidden="true" />

      <header className="hero">
        <div className="hero-top">
          <div className="brand">
            <span className="brand-gem">◆</span>
            <div>
              <h1>Shop Hunter</h1>
              <p className="tagline">Every grocery and gear deal, ever emailed.</p>
            </div>
          </div>
          <button className="theme-btn" onClick={toggleTheme} aria-label="Toggle colour theme">
            <span className="theme-icon">{theme === "dark" ? "☀" : "☾"}</span>
          </button>
        </div>

        {stats && (
          <div className="stat-row">
            <Stat value={stats.total} label="deals tracked" />
            <Stat value={stats.strong} label="strong buys" accent="diamond" />
            <Stat value={stats.bestSaving != null ? `€${fmtEur(stats.bestSaving)}` : "—"} label="best saving" />
            <Stat value={fmtDate(stats.latest)} label="last run" small />
          </div>
        )}
      </header>

      <div className="toolbar">
        <div className="search-wrap">
          <span className="search-icon">⌕</span>
          <input
            className="search"
            placeholder="Search a product or retailer…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button className="search-clear" onClick={() => setQuery("")} aria-label="Clear search">
              ✕
            </button>
          )}
        </div>

        <div className="segmented" role="group" aria-label="Filter by verdict">
          {Object.keys(VERDICT).map((v) => (
            <button
              key={v}
              className={`seg seg-${v}` + (verdicts[v] ? " on" : "")}
              onClick={() => setVerdicts({ ...verdicts, [v]: !verdicts[v] })}
              aria-pressed={verdicts[v]}
            >
              <span className="seg-glyph">{VERDICT[v].glyph}</span>
              {VERDICT[v].label}
              <span className="seg-count">{counts[v]}</span>
            </button>
          ))}
        </div>

        <div className="select-wrap">
          <select value={skuClass} onChange={(e) => setSkuClass(e.target.value)} aria-label="Filter by class">
            <option value="all">All classes</option>
            <option value="consumable">Consumable</option>
            <option value="durable">Durable</option>
          </select>
          <span className="select-caret">▾</span>
        </div>

        <div className="select-wrap">
          <select value={retailer} onChange={(e) => setRetailer(e.target.value)} aria-label="Filter by retailer">
            <option value="all">All retailers</option>
            {retailers.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <span className="select-caret">▾</span>
        </div>

        <div className="select-wrap">
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} aria-label="Sort deals">
            <option value="date">Newest first</option>
            <option value="score">Highest score</option>
            <option value="price">Lowest price</option>
          </select>
          <span className="select-caret">▾</span>
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="empty-grid">
          <span className="empty-gem">◇</span>
          <p>No deals match these filters.</p>
        </div>
      ) : (
        <div className="grid">
          {visible.map((e, i) => (
            <DealCard
              key={entryKey(e)}
              entry={e}
              index={i}
              onOpen={() => setSelectedKey(entryKey(e))}
            />
          ))}
        </div>
      )}

      <footer className="site-foot">
        <span className="brand-gem small">◆</span>
        Everything that ever made it to the inbox — nothing more, nothing less.
      </footer>

      <Drawer entry={selected} onClose={() => setSelectedKey(null)} />
    </div>
  );
}

function Stat({ value, label, accent, small }) {
  return (
    <div className={`stat${accent ? ` stat-${accent}` : ""}`}>
      <div className={`stat-value${small ? " sm" : ""}`}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function ScoreRing({ score, slug, size = 56 }) {
  const stroke = 5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score ?? 0));
  const off = c * (1 - pct / 100);
  return (
    <svg className="ring" width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle className="ring-track" cx={size / 2} cy={size / 2} r={r} strokeWidth={stroke} fill="none" />
      <circle
        className="ring-value"
        cx={size / 2}
        cy={size / 2}
        r={r}
        strokeWidth={stroke}
        fill="none"
        stroke={`url(#grad-${slug || "skip"})`}
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={off}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text className="ring-text" x="50%" y="50%" dominantBaseline="central" textAnchor="middle">
        {score ?? "—"}
      </text>
    </svg>
  );
}

// Consumable bulk/par line: "€9.80/kg vs €12.00 par (18% under) · buy 5 kg = €49.00, saves €11.00"
function consumableLine(e) {
  const up = fmt2(e.unit_price_eur);
  if (!up) return null;
  const suffix = unitSuffix(e.unit);
  let s = `€${up}${suffix}`;
  if (e.par_eur != null) {
    const par = fmt2(e.par_eur);
    const pct = fmtPct(e.discount);
    s += ` vs €${par} par${pct ? ` (${pct} under)` : ""}`;
  }
  if (e.qty != null && e.price_eur != null && e.saving_eur != null) {
    const qty = Number.isInteger(e.qty) ? `${e.qty}` : e.qty.toFixed(2);
    s += ` · buy ${qty} ${e.unit || ""} = €${fmt2(e.price_eur)}, saves €${fmt2(e.saving_eur)}`;
  }
  return s;
}

// Durable saving line: "€179 (normal €349, 49% off, saves €170)"
// or, with a trigger and no reference price: "€179 (trigger €200)"
function durableLine(e) {
  const price = fmtEur(e.price_eur);
  if (!price) return null;
  const bits = [];
  if (e.reference_price_eur != null) bits.push(`normal €${fmtEur(e.reference_price_eur)}`);
  const pct = fmtPct(e.discount);
  if (pct) bits.push(`${pct} off`);
  if (e.saving_eur != null) bits.push(`saves €${fmt2(e.saving_eur)}`);
  if (e.trigger_eur != null) bits.push(`trigger €${fmtEur(e.trigger_eur)}`);
  return `€${price}${bits.length ? ` (${bits.join(", ")})` : ""}`;
}

function DealCard({ entry: e, index, onOpen }) {
  const slug = verdictSlug(e.verdict);
  const title = e.label || e.name || "Unnamed product";
  const isConsumable = e.sku_class === "consumable";
  const line = isConsumable ? consumableLine(e) : durableLine(e);
  const blurb = e.value_case || e.about || "";
  return (
    <button
      className={`card verdict-${slug}`}
      style={{ animationDelay: `${Math.min(index, 12) * 45}ms` }}
      onClick={onOpen}
    >
      <div className="card-sheen" aria-hidden="true" />
      <div className="card-head">
        <span className={`badge verdict-${slug}`}>
          <span className="badge-glyph">{VERDICT[slug]?.glyph}</span>
          {VERDICT[slug]?.label || e.verdict}
        </span>
        <ScoreRing score={e.fit_score} slug={slug} />
      </div>

      <h3 className="card-dest">{title}</h3>
      <div className="card-window">
        <span className="ico">🏬</span>
        {e.retailer || ""}
      </div>

      <div className="card-price">
        {line ? (
          <span className="price-eur">{line}</span>
        ) : (
          <span className="price-none">Price on request</span>
        )}
      </div>

      {blurb && <p className="card-blurb">{blurb}</p>}

      <div className="card-foot">
        {e.valid_until ? (
          <span className="pill pill-muted">valid until {fmtDate(e.valid_until)}</span>
        ) : (
          <span className="pill pill-muted">no expiry given</span>
        )}
        <span className="card-open">Details →</span>
      </div>
    </button>
  );
}

function Drawer({ entry: e, onClose }) {
  useEffect(() => {
    if (!e) return;
    const onKey = (ev) => ev.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [e, onClose]);

  if (!e) return null;
  const slug = verdictSlug(e.verdict);
  const title = e.label || e.name || "Unnamed product";
  const isConsumable = e.sku_class === "consumable";
  const line = isConsumable ? consumableLine(e) : durableLine(e);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className={`drawer verdict-${slug}`} onClick={(ev) => ev.stopPropagation()} role="dialog" aria-modal="true">
        <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>

        <div className="drawer-hero">
          <span className={`badge verdict-${slug}`}>
            <span className="badge-glyph">{VERDICT[slug]?.glyph}</span>
            {VERDICT[slug]?.label || e.verdict}
          </span>
          <h2>{title}</h2>
          <div className="drawer-sub">
            {e.retailer || ""} {e.sku_class ? `· ${e.sku_class}` : ""}
          </div>
        </div>

        <div className="scorebar">
          <ScoreRing score={e.fit_score} slug={slug} size={72} />
          <div className="scorebar-break">
            <div className="score-final">
              fit score = <b>{e.fit_score ?? "—"}</b><span>/100 · {e.verdict}</span>
            </div>
          </div>
        </div>

        {e.about && <p className="d-about">{e.about}</p>}
        {e.value_case && (
          <div className="value-case">
            <span className="vc-label">Why it’s a deal</span>
            {e.value_case}
          </div>
        )}

        {line && (
          <div className="price-banner">
            <div>
              <span className="pb-eur">{line}</span>
            </div>
          </div>
        )}

        {e.the_math && <div className="d-note"><b>The math:</b> {e.the_math}</div>}

        {isConsumable && e.trigger_eur == null && e.reference_price_eur == null && !e.par_eur && null}

        {!isConsumable && e.trigger_eur != null && (
          <div className="d-note">Trigger price: €{fmt2(e.trigger_eur)}</div>
        )}

        {e.bulk_advice && <div className="d-note"><b>Bulk advice:</b> {e.bulk_advice}</div>}

        {e.market_insight && <p className="d-about">{e.market_insight}</p>}

        {e.valid_until && (
          <div className="d-note">Valid until {fmtDate(e.valid_until)}</div>
        )}

        {e.url && (
          <a className="book-btn" href={e.url} target="_blank" rel="noreferrer">
            View offer ↗
          </a>
        )}

        {e.red_flags && <div className="d-note flag">🚩 {e.red_flags}</div>}

        <div className="drawer-meta">
          {e.evidence != null && (
            <span className="pill conf">Evidence: {e.evidence}</span>
          )}
        </div>
      </aside>
    </div>
  );
}

function Chip({ n, label, tone }) {
  return (
    <span className={`chip${tone ? ` chip-${tone}` : ""}`}>
      <b>{n ?? "—"}</b>
      <span>{label}</span>
    </span>
  );
}

function GradientDefs() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        <linearGradient id="grad-strong" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#67e8f9" />
          <stop offset="1" stopColor="#3b82f6" />
        </linearGradient>
        <linearGradient id="grad-fair" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#fbbf24" />
          <stop offset="1" stopColor="#f97316" />
        </linearGradient>
        <linearGradient id="grad-skip" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#cbd5e1" />
          <stop offset="1" stopColor="#94a3b8" />
        </linearGradient>
      </defs>
    </svg>
  );
}
