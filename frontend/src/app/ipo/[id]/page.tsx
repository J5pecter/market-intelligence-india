import { IpoDetail } from "./IpoDetail";
import { Panel, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function IpoDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [detail, research, gmp] = await Promise.all([
    apiFetch<any>(`/api/ipo/${id}`, { auth: false }),
    apiFetch<any>(`/api/ipo/${id}/research`, { auth: false }),
    apiFetch<any>(`/api/ipo/${id}/gmp`, { auth: false }),
  ]);

  if (!detail.data) {
    return (
      <Panel title="IPO">
        <Unavailable reason={detail.error} />
      </Panel>
    );
  }

  return (
    <IpoDetail
      id={id}
      detail={detail.data}
      research={research.data}
      gmp={gmp.data}
    />
  );
}
