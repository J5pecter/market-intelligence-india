import { StockDetail } from "./StockDetail";
import { Panel, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function StockPage({
  params,
  searchParams,
}: {
  params: Promise<{ symbol: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { symbol } = await params;
  const query = await searchParams;
  const upper = symbol.toUpperCase();

  const result = await apiFetch<any>(`/api/stocks/${upper}`, { auth: false });

  if (!result.data) {
    return (
      <Panel title={upper}>
        <Unavailable
          reason={result.error}
          hint={[
            "The symbol may not be in this deployment's instrument master.",
            "Run instrument_sync from the admin panel, or add it manually.",
          ]}
        />
      </Panel>
    );
  }

  const tab = (query.tab as never) || "overview";

  return <StockDetail symbol={upper} overview={result.data} initialTab={tab} />;
}
