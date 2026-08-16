import { OptionChainView } from "./OptionChainView";

export const dynamic = "force-dynamic";

export default async function OptionChainPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  return <OptionChainView initialSymbol={(params.symbol || "NIFTY").toUpperCase()} />;
}
