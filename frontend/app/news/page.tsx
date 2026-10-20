import { ActionButton } from "@/components/Actions";
import { Badge, Card, EmptyState, ErrorBox, PageHeader, Table, Td } from "@/components/ui";
import { tryApi } from "@/lib/backend";
import { utc } from "@/lib/format";
import type { NewsEvent } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function NewsPage() {
  const now = new Date();
  const start = new Date(now.getTime() - 7 * 864e5).toISOString();
  const end = new Date(now.getTime() + 7 * 864e5).toISOString();
  const res = await tryApi<{ items: NewsEvent[]; coverage: { weeks_in_range: number; weeks_with_events: number } }>(
    `/api/news/events?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);

  return (
    <>
      <PageHeader title="News calendar" subtitle="Optional Forex Factory calendar export, used to block new entries around high-impact events."
        actions={<ActionButton path="news/refresh" label="Fetch calendar" busyLabel="Fetching…" />} />
      <Card title="Events from 7 days ago to 7 days ahead (UTC)">
        {res.error ? <ErrorBox error={res.error} /> : !res.data?.items.length ? (
          <EmptyState title="No calendar events stored">
            Set TRADE1_NEWS_FEED_URL in backend\.env to the calendar&apos;s JSON export link, run <code>python -m app.cli news-check</code>, then click “Fetch calendar”. History only builds up from the day you start collecting.
          </EmptyState>
        ) : (
          <Table head={["Time (UTC)", "Currency", "Impact", "Event", "Forecast", "Previous"]} dense>
            {res.data.items.map((e) => (
              <tr key={`${e.event_time}-${e.currency}-${e.title}`}>
                <Td>{utc(e.event_time)}</Td><Td>{e.currency}</Td>
                <Td><Badge tone={e.impact === "high" ? "bad" : e.impact === "medium" ? "warn" : "neutral"}>{e.impact}</Badge></Td>
                <Td>{e.title}</Td><Td>{e.forecast ?? "—"}</Td><Td>{e.previous ?? "—"}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </>
  );
}
