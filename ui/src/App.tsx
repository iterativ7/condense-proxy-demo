import { useEffect, useMemo, useState } from "react";

type OptimizationSummary = {
  optimization_id: string;
  events: number;
  total_savings_usd: number;
  total_tokens_saved: number;
  last_technique: string | null;
  last_action: string | null;
  last_details: Record<string, unknown>;
};

type SummaryResponse = {
  overall: {
    total_savings_usd: number;
    total_tokens_saved_estimate: number;
    total_requests: number;
    uptime_seconds: number;
  };
  window: "24h" | "7d" | "30d" | "all_time";
  enabled_tabs: string[];
  optimizations: OptimizationSummary[];
  series: Array<{
    bucket: string;
    total_requests: number;
    total_savings_usd: number;
    total_tokens_saved_estimate: number;
  }>;
  optimization_series: Array<{
    optimization_id: string;
    points: Array<{
      bucket: string;
      events: number;
      total_savings_usd: number;
      total_tokens_saved: number;
    }>;
  }>;
};

const EMPTY_SUMMARY: SummaryResponse = {
  overall: {
    total_savings_usd: 0,
    total_tokens_saved_estimate: 0,
    total_requests: 0,
    uptime_seconds: 0,
  },
  window: "7d",
  enabled_tabs: [],
  optimizations: [],
  series: [],
  optimization_series: [],
};

function formatUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

function formatInt(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

export function App() {
  const [summary, setSummary] = useState<SummaryResponse>(EMPTY_SUMMARY);
  const [window, setWindow] = useState<SummaryResponse["window"]>("7d");
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("Loading...");

  useEffect(() => {
    let mounted = true;

    async function loadSummary() {
      try {
        const response = await fetch(`/metrics/summary/v2?window=${window}`, { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = (await response.json()) as SummaryResponse;
        if (!mounted) {
          return;
        }
        setSummary(data);
        setStatus(`Last updated: ${new Date().toLocaleTimeString()}`);
        if (data.enabled_tabs.length > 0 && !activeTab) {
          setActiveTab(data.enabled_tabs[0]);
        }
        if (activeTab && !data.enabled_tabs.includes(activeTab)) {
          setActiveTab(data.enabled_tabs[0] ?? null);
        }
      } catch (error) {
        if (!mounted) {
          return;
        }
        const message = error instanceof Error ? error.message : "Unknown error";
        setStatus(`Failed to refresh metrics: ${message}`);
      }
    }

    void loadSummary();
    const timer = setInterval(() => {
      void loadSummary();
    }, 5000);

    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, [activeTab, window]);

  const optimizationMap = useMemo(() => {
    return new Map(summary.optimizations.map((entry) => [entry.optimization_id, entry]));
  }, [summary.optimizations]);

  const selected = activeTab ? optimizationMap.get(activeTab) : undefined;
  const selectedSeries = summary.optimization_series.find((entry) => entry.optimization_id === activeTab);
  const latestOverallPoint = summary.series[summary.series.length - 1];

  return (
    <main className="container">
      <header>
        <h1>Condense Modular Savings UI</h1>
        <p className="subtitle">
          Consolidated savings + optimization-level breakdown (auto-refresh every 5 seconds)
        </p>
      </header>

      <section className="overview-grid">
        <article className="card">
          <span className="label">Overall USD Savings</span>
          <span className="value value-good">{formatUsd(summary.overall.total_savings_usd)}</span>
        </article>
        <article className="card">
          <span className="label">Overall Token Savings (Estimate)</span>
          <span className="value value-accent">{formatInt(summary.overall.total_tokens_saved_estimate)}</span>
        </article>
        <article className="card">
          <span className="label">Total Requests</span>
          <span className="value">{formatInt(summary.overall.total_requests)}</span>
        </article>
        <article className="card">
          <span className="label">Uptime (seconds)</span>
          <span className="value">{formatInt(summary.overall.uptime_seconds)}</span>
        </article>
      </section>

      <section className="window-section">
        <h2>Time Window</h2>
        <div className="tabs">
          {(["24h", "7d", "30d", "all_time"] as const).map((option) => (
            <button
              key={option}
              className={option === window ? "tab tab-active" : "tab"}
              onClick={() => setWindow(option)}
            >
              {option}
            </button>
          ))}
        </div>
      </section>

      <section className="overview-grid">
        <article className="card">
          <span className="label">Trend Buckets ({window})</span>
          <span className="value">{formatInt(summary.series.length)}</span>
        </article>
        <article className="card">
          <span className="label">Latest Bucket Savings</span>
          <span className="value value-good">{formatUsd(latestOverallPoint?.total_savings_usd ?? 0)}</span>
        </article>
        <article className="card">
          <span className="label">Latest Bucket Requests</span>
          <span className="value">{formatInt(latestOverallPoint?.total_requests ?? 0)}</span>
        </article>
      </section>

      <section className="tabs-section">
        <h2>Optimization Tabs</h2>
        {summary.enabled_tabs.length === 0 ? (
          <p className="muted">No optimization tabs are enabled in the current config.</p>
        ) : (
          <div className="tabs">
            {summary.enabled_tabs.map((tabId) => (
              <button
                key={tabId}
                className={tabId === activeTab ? "tab tab-active" : "tab"}
                onClick={() => setActiveTab(tabId)}
              >
                {tabId}
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="detail-section">
        {selected ? (
          <article className="card detail-card">
            <h3>{selected.optimization_id}</h3>
            <div className="detail-grid">
              <div>
                <span className="label">Events</span>
                <span className="value">{formatInt(selected.events)}</span>
              </div>
              <div>
                <span className="label">USD Savings</span>
                <span className="value">{formatUsd(selected.total_savings_usd)}</span>
              </div>
              <div>
                <span className="label">Tokens Saved</span>
                <span className="value">{formatInt(selected.total_tokens_saved)}</span>
              </div>
              <div>
                <span className="label">Last Action</span>
                <span className="value-small">{selected.last_action ?? "n/a"}</span>
              </div>
              <div>
                <span className="label">Last Technique</span>
                <span className="value-small">{selected.last_technique ?? "n/a"}</span>
              </div>
            </div>
            <details>
              <summary>Last Details Payload</summary>
              <pre>{JSON.stringify(selected.last_details, null, 2)}</pre>
            </details>
            <details>
              <summary>Historical Series ({window})</summary>
              <pre>{JSON.stringify(selectedSeries?.points ?? [], null, 2)}</pre>
            </details>
          </article>
        ) : (
          <p className="muted">Select an optimization tab to view detailed savings.</p>
        )}
      </section>

      <footer className="status">{status}</footer>
    </main>
  );
}
