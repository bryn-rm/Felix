"use client";

import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

export interface SentimentPoint {
  week: string;
  score: number;
}

interface RelationshipChartProps {
  data: SentimentPoint[];
  trend?: string | null;
}

function trendColor(trend: string | null | undefined): string {
  if (trend === "improving") return "#22c55e"; // green-500
  if (trend === "deteriorating") return "#ef4444"; // red-500
  return "#94a3b8"; // slate-400
}

interface TooltipPayload {
  payload: SentimentPoint;
}

function ChartTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
}) {
  if (!active || !payload?.length) return null;
  const { week, score } = payload[0].payload;
  return (
    <div className="rounded border border-slate-600 bg-slate-800 px-2.5 py-1.5 text-xs shadow-lg">
      <p className="text-slate-400">{week}</p>
      <p className="font-semibold text-slate-100">{score.toFixed(2)}</p>
    </div>
  );
}

export function RelationshipChart({ data, trend }: RelationshipChartProps) {
  const color = trendColor(trend);

  return (
    <ResponsiveContainer width="100%" height={80}>
      <LineChart data={data} margin={{ top: 6, right: 6, bottom: 6, left: 6 }}>
        <XAxis dataKey="week" hide />
        <YAxis domain={[0, 1]} hide />
        <Tooltip content={<ChartTooltip />} />
        <Line
          type="monotone"
          dataKey="score"
          stroke={color}
          strokeWidth={2}
          dot={{ r: 3, fill: color, strokeWidth: 0 }}
          activeDot={{ r: 5, fill: color }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// Helper: generate synthetic 8-week history from a trend string + base score
// ---------------------------------------------------------------------------

/** Deterministic pseudo-random based on a numeric seed. */
function pr(seed: number): number {
  const x = Math.sin(seed + 1.7) * 10_000;
  return x - Math.floor(x);
}

export function generateSentimentHistory(
  trend: string | null | undefined,
  baseScore: number,
  seed: number,
): SentimentPoint[] {
  const WEEKS = 8;
  const now = new Date();

  return Array.from({ length: WEEKS }, (_, i) => {
    const d = new Date(now);
    d.setDate(d.getDate() - (WEEKS - 1 - i) * 7);
    const week = d.toLocaleDateString("en", { month: "short", day: "numeric" });

    const progress = i / (WEEKS - 1); // 0 → 1 across weeks
    const jitter = (pr(seed + i) - 0.5) * 0.1;

    let score: number;
    if (trend === "improving") {
      score = Math.max(0.1, Math.min(0.99, baseScore - 0.15 + progress * 0.25 + jitter));
    } else if (trend === "deteriorating") {
      score = Math.max(0.05, Math.min(0.95, baseScore + 0.1 - progress * 0.2 + jitter));
    } else {
      score = Math.max(0.1, Math.min(0.9, baseScore + jitter));
    }

    return { week, score: Math.round(score * 100) / 100 };
  });
}
