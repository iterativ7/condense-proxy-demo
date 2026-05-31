import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

type MockSummary = {
  overall: {
    total_savings_usd: number;
    total_tokens_saved_estimate: number;
    total_requests: number;
    uptime_seconds: number;
  };
  window: "24h" | "7d" | "30d" | "all_time";
  enabled_tabs: string[];
  optimizations: Array<{
    optimization_id: string;
    events: number;
    total_savings_usd: number;
    total_tokens_saved: number;
    last_technique: string | null;
    last_action: string | null;
    last_details: Record<string, unknown>;
  }>;
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

const summaryByWindow: Record<string, MockSummary> = {
  "7d": {
    overall: {
      total_savings_usd: 12.34,
      total_tokens_saved_estimate: 1530,
      total_requests: 42,
      uptime_seconds: 3600,
    },
    window: "7d",
    enabled_tabs: ["cache"],
    optimizations: [
      {
        optimization_id: "cache",
        events: 7,
        total_savings_usd: 9.87,
        total_tokens_saved: 1234,
        last_technique: "exact",
        last_action: "cache_hit",
        last_details: { similarity: 1.0 },
      },
    ],
    series: [
      {
        bucket: "2026-05-24T00:00:00Z",
        total_requests: 20,
        total_savings_usd: 5.0,
        total_tokens_saved_estimate: 500,
      },
      {
        bucket: "2026-05-25T00:00:00Z",
        total_requests: 22,
        total_savings_usd: 7.34,
        total_tokens_saved_estimate: 1030,
      },
    ],
    optimization_series: [
      {
        optimization_id: "cache",
        points: [
          {
            bucket: "2026-05-24T00:00:00Z",
            events: 3,
            total_savings_usd: 4.0,
            total_tokens_saved: 400,
          },
          {
            bucket: "2026-05-25T00:00:00Z",
            events: 4,
            total_savings_usd: 5.87,
            total_tokens_saved: 834,
          },
        ],
      },
    ],
  },
  "24h": {
    overall: {
      total_savings_usd: 2.5,
      total_tokens_saved_estimate: 250,
      total_requests: 5,
      uptime_seconds: 3600,
    },
    window: "24h",
    enabled_tabs: ["cache"],
    optimizations: [
      {
        optimization_id: "cache",
        events: 2,
        total_savings_usd: 2.5,
        total_tokens_saved: 250,
        last_technique: "exact",
        last_action: "cache_hit",
        last_details: { similarity: 1.0 },
      },
    ],
    series: [
      {
        bucket: "2026-05-25T12:00:00Z",
        total_requests: 2,
        total_savings_usd: 1.0,
        total_tokens_saved_estimate: 100,
      },
      {
        bucket: "2026-05-25T13:00:00Z",
        total_requests: 3,
        total_savings_usd: 1.5,
        total_tokens_saved_estimate: 150,
      },
    ],
    optimization_series: [
      {
        optimization_id: "cache",
        points: [
          {
            bucket: "2026-05-25T12:00:00Z",
            events: 1,
            total_savings_usd: 1.0,
            total_tokens_saved: 100,
          },
          {
            bucket: "2026-05-25T13:00:00Z",
            events: 1,
            total_savings_usd: 1.5,
            total_tokens_saved: 150,
          },
        ],
      },
    ],
  },
};

describe("dashboard ui metrics rendering", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input);
        const window = url.includes("window=24h") ? "24h" : "7d";
        return {
          ok: true,
          status: 200,
          json: async () => summaryByWindow[window],
        } as Response;
      }),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows expected savings state from API payload", async () => {
    render(<App />);

    expect(await screen.findByText("$12.3400")).toBeInTheDocument();
    expect(screen.getByText("1,530")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("$9.8700")).toBeInTheDocument();
    expect(screen.getByText("1,234")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("$7.3400")).toBeInTheDocument();
    expect(screen.getByText("22")).toBeInTheDocument();
  });

  it("switches window and requests matching query data", async () => {
    render(<App />);
    await screen.findByText("$12.3400");

    fireEvent.click(screen.getByRole("button", { name: "24h" }));

    await waitFor(() => {
      expect(screen.getByText("Trend Buckets (24h)")).toBeInTheDocument();
    });
    expect(screen.getByText("$1.5000")).toBeInTheDocument();
    expect(screen.getAllByText("250").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("5").length).toBeGreaterThanOrEqual(1);

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledWith("/metrics/summary/v2?window=24h", { cache: "no-store" });
  });
});
