import { Notice, Panel, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { dateTimeIST, titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function CompliancePage() {
  const result = await apiFetch<any>("/api/compliance/documents", { auth: false });
  if (!result.data) {
    return <Panel title="Compliance"><Unavailable reason={result.error} /></Panel>;
  }

  const { configuration, documents, governance_note, review_overdue } = result.data;

  return (
    <div className="space-y-3">
      <Panel title="How this platform describes itself">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Tag tone={configuration.is_registered ? "accent" : "neutral"}>
              {configuration.descriptor}
            </Tag>
            {review_overdue && <Tag tone="warn">Review overdue</Tag>}
          </div>

          {configuration.is_registered ? (
            <Notice tone="info">
              <strong>{configuration.verification_badge.label}</strong> —{" "}
              {configuration.verification_badge.number}, held by{" "}
              {configuration.verification_badge.entity}. Reviewed by{" "}
              {configuration.verification_badge.reviewed_by || "no one recorded"} on{" "}
              {configuration.verification_badge.reviewed_on || "an unrecorded date"}.{" "}
              {configuration.verification_badge.caveat}
            </Notice>
          ) : (
            <Notice tone="warn">
              No registration is configured for this deployment. The platform
              therefore displays no verification badge of any kind, and describes
              itself only as an{" "}
              <strong>{configuration.descriptor.toLowerCase()}</strong>. It will
              refuse to publish text containing any of the prohibited claims
              listed below.
            </Notice>
          )}

          <div className="scroll-x">
            <table className="w-full min-w-[520px]">
              <thead>
                <tr>
                  <th className="th">Registration</th>
                  <th className="th">Status</th>
                  <th className="th">Number</th>
                  <th className="th">Displayed as</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/50">
                {configuration.registration_claims.map((claim: any) => (
                  <tr key={claim.kind}>
                    <td className="td text-ink">{claim.kind}</td>
                    <td className="td">
                      <Tag tone={claim.verified ? "pos" : "neutral"}>
                        {claim.status.replace(/_/g, " ")}
                      </Tag>
                    </td>
                    <td className="num td text-ink-dim">{claim.number || "—"}</td>
                    <td className="td text-2xs text-ink-muted">{claim.display}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <dl className="grid gap-x-6 gap-y-1 text-2xs sm:grid-cols-2">
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Entity name</dt>
              <dd className="text-ink-dim">{configuration.entity_name || "—"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Legal entity</dt>
              <dd className="text-ink-dim">{configuration.legal_entity_name || "not configured"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Effective date</dt>
              <dd className="text-ink-dim">{configuration.effective_date || "not set"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Last reviewed</dt>
              <dd className="text-ink-dim">{configuration.last_reviewed_date || "never"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Legal reviewer</dt>
              <dd className="text-ink-dim">{configuration.legal_reviewer || "none recorded"}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-ink-muted">Disclaimer version</dt>
              <dd className="text-ink-dim">{configuration.disclaimers.version}</dd>
            </div>
          </dl>
        </div>
      </Panel>

      <Panel title="Disclosures">
        <div className="space-y-3">
          {(["primary", "derivatives", "generated_signal", "external_research", "gmp"] as const).map((key) => (
            <div key={key}>
              <h3 className="mb-1 text-2xs font-semibold uppercase tracking-wider text-ink-muted">
                {titleCase(key)}
              </h3>
              <p className="text-xs leading-relaxed text-ink-dim">
                {configuration.disclaimers[key]}
              </p>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="Statistical claims" subtitle="Every figure carries the study period it came from">
        {configuration.statistical_claims?.length ? (
          <ul className="space-y-2">
            {configuration.statistical_claims.map((claim: any) => (
              <li key={claim.id} className="rounded border border-line bg-raised/30 p-2.5">
                <p className="text-xs leading-relaxed text-ink-dim">{claim.claim}</p>
                <p className="mt-1 text-2xs text-ink-muted">
                  Study period: <strong>{claim.study_period}</strong> · Source:{" "}
                  {claim.source_url ? (
                    <a href={claim.source_url} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
                      {claim.source_name}
                    </a>
                  ) : claim.source_name}
                </p>
                <p className="mt-0.5 text-2xs text-warn">
                  {claim.verified_on
                    ? `Verified on ${claim.verified_on} by ${claim.verified_by || "an unrecorded reviewer"}.`
                    : "Not yet verified against the current source by this deployment. It is withheld entirely in PRODUCTION."}
                </p>
                <p className="mt-0.5 text-2xs text-ink-muted">{claim.display_rule}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-2xs text-ink-muted">
            No statistical claims are configured for display.
          </p>
        )}
      </Panel>

      <Panel title="Claims this deployment will not make">
        <div className="flex flex-wrap gap-1.5">
          {configuration.prohibited_claims.map((claim: string) => (
            <Tag key={claim} tone="neg">{claim}</Tag>
          ))}
        </div>
        <p className="mt-2 text-2xs leading-relaxed text-ink-muted">
          Any research call, rationale or invalidation text containing one of
          these phrases is rejected by the API before it can be saved.
        </p>
      </Panel>

      <Panel title="Tracked regulatory sources" subtitle={governance_note} bodyClassName="p-0">
        <div className="scroll-x">
          <table className="w-full min-w-[820px]">
            <thead className="border-b border-line">
              <tr>
                <th className="th">Document</th>
                <th className="th">Regulator</th>
                <th className="th">Type</th>
                <th className="th">Version</th>
                <th className="th">Status</th>
                <th className="th">Last checked</th>
                <th className="th">By</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line/50">
              {documents.map((document: any, index: number) => (
                <tr key={index}>
                  <td className="td max-w-[320px]">
                    {document.url ? (
                      <a href={document.url} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
                        {document.name}
                      </a>
                    ) : (
                      <span className="text-ink">{document.name}</span>
                    )}
                    {document.summary && (
                      <p className="mt-0.5 whitespace-normal text-2xs text-ink-muted">
                        {document.summary}
                      </p>
                    )}
                  </td>
                  <td className="td">{document.regulator}</td>
                  <td className="td text-2xs">{titleCase(document.document_type)}</td>
                  <td className="td text-2xs text-ink-muted">{document.version || "—"}</td>
                  <td className="td">
                    <Tag tone={document.status === "VERIFIED" ? "pos" : "warn"}>
                      {document.status}
                    </Tag>
                  </td>
                  <td className="td text-2xs text-ink-muted">
                    {dateTimeIST(document.last_checked_at)}
                  </td>
                  <td className="td text-2xs text-ink-muted">{document.checked_by || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Reference links">
        <ul className="space-y-1.5">
          {(configuration.source_urls || []).map((source: any) => (
            <li key={source.name} className="text-xs">
              <a href={source.url} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
                {source.name}
              </a>
              {source.note && (
                <p className="mt-0.5 text-2xs text-ink-muted">{source.note}</p>
              )}
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
