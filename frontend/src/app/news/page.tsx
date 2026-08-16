import Link from "next/link";
import { Disclosure, Empty, Panel, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { dateTimeIST, num, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function NewsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const query = new URLSearchParams({ limit: "80" });
  if (params.symbol) query.set("symbol", params.symbol.toUpperCase());
  if (params.category) query.set("category", params.category);
  if (params.sentiment) query.set("sentiment", params.sentiment);
  if (params.min_impact) query.set("min_impact", params.min_impact);

  const result = await apiFetch<any>(`/api/news?${query}`, { auth: false });
  if (!result.data) {
    return (
      <Panel title="News">
        <Unavailable reason={result.error} />
      </Panel>
    );
  }
  const { articles, categories, note } = result.data;

  return (
    <Panel
      title="News feed"
      subtitle={note}
      actions={
        <form action="/news" method="get" className="flex flex-wrap items-center gap-1.5">
          <input name="symbol" defaultValue={params.symbol || ""} placeholder="Symbol" className="field w-28" />
          <select name="category" defaultValue={params.category || ""} className="field w-40">
            <option value="">All categories</option>
            {categories.map((category: string) => (
              <option key={category} value={category}>{titleCase(category)}</option>
            ))}
          </select>
          <select name="sentiment" defaultValue={params.sentiment || ""} className="field w-32">
            <option value="">Any sentiment</option>
            <option value="POSITIVE">Positive</option>
            <option value="NEUTRAL">Neutral</option>
            <option value="NEGATIVE">Negative</option>
          </select>
          <input name="min_impact" type="number" min="0" max="100" defaultValue={params.min_impact || ""} placeholder="Min impact" className="field w-24" />
          <button className="btn" type="submit">Filter</button>
        </form>
      }
    >
      {articles.length === 0 ? (
        <Empty message="No articles stored yet. Run the news_refresh job from the admin panel." />
      ) : (
        <ul className="divide-y divide-line/60">
          {articles.map((article: any) => (
            <li key={article.id} className="py-3 first:pt-0">
              <div className="flex items-start justify-between gap-3">
                <a
                  href={article.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="min-w-0 flex-1 text-xs font-medium leading-relaxed text-ink hover:text-accent"
                >
                  {article.headline}
                </a>
                <div className="flex shrink-0 items-center gap-1.5">
                  {article.category && <Tag tone="neutral">{titleCase(article.category)}</Tag>}
                  {article.sentiment && (
                    <Tag tone={article.sentiment === "POSITIVE" ? "pos" : article.sentiment === "NEGATIVE" ? "neg" : "neutral"}>
                      {article.sentiment}
                    </Tag>
                  )}
                  {article.impact_score !== null && (
                    <Tag tone="accent">{num(article.impact_score, 0)}</Tag>
                  )}
                </div>
              </div>
              <p className="mt-0.5 text-2xs text-ink-muted">
                {article.publisher} · {dateTimeIST(article.published_at)}
                {article.symbol && (
                  <>
                    {" · "}
                    <Link href={`/stocks/${article.symbol}`} className="text-accent">
                      {article.symbol}
                    </Link>
                  </>
                )}
                {article.is_demo && <span className="ml-1 text-warn">DEMO</span>}
              </p>
              {article.explanation && (
                <Disclosure summary="How this impact score was built">
                  <p className="text-2xs leading-relaxed text-ink-dim">
                    {article.explanation.text}
                  </p>
                  <dl className="mt-2 grid gap-x-4 gap-y-0.5 text-2xs sm:grid-cols-2">
                    {Object.entries(article.explanation.components || {}).map(([key, value]) => (
                      <div key={key} className="flex justify-between gap-2">
                        <dt className="text-ink-muted">{titleCase(key)}</dt>
                        <dd className="num text-ink">{value === null ? "n/a" : String(value)}</dd>
                      </div>
                    ))}
                  </dl>
                  <ul className="mt-2 list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
                    {(article.explanation.limitations || []).map((limit: string) => (
                      <li key={limit}>{limit}</li>
                    ))}
                  </ul>
                </Disclosure>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
