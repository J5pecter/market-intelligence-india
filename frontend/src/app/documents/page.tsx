"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AuthGate, CurrentUser } from "@/components/AuthGate";
import {
  DataBadge, Disclosure, Empty, Loading, Notice, Panel, Stat, Tag, Unavailable,
} from "@/components/primitives";
import { api, getToken } from "@/lib/api";
import { DASH, compactInr, dateIST, dateTimeIST, num, titleCase } from "@/lib/format";

const DOC_TYPES = [
  "ANNUAL_REPORT", "QUARTERLY_RESULT", "INVESTOR_PRESENTATION",
  "EARNINGS_RELEASE", "TRANSCRIPT", "EXCHANGE_FILING", "ANNOUNCEMENT",
  "DRHP", "RHP", "OFFER_DOCUMENT", "CREDIT_RATING", "SHAREHOLDING",
];

export default function DocumentsPage() {
  return <AuthGate requireRole="ANALYST">{(user) => <DocumentsView user={user} />}</AuthGate>;
}

function DocumentsView({ user }: { user: CurrentUser }) {
  const [tab, setTab] = useState<"library" | "queue">("library");
  const [documents, setDocuments] = useState<any[]>([]);
  const [queue, setQueue] = useState<any>(null);
  const [selected, setSelected] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [reviewNote, setReviewNote] = useState<string>("");

  const load = useCallback(async () => {
    const [list, pending] = await Promise.all([
      api.get<any>("/api/documents", false),
      api.get<any>("/api/documents/citations/queue", false),
    ]);
    if (list.data) {
      setDocuments(list.data.documents);
      setReviewNote(list.data.review_note);
    }
    if (pending.data) setQueue(pending.data);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openDocument = async (id: string) => {
    const response = await api.get<any>(`/api/documents/${id}`, false);
    setSelected(response.data ?? { error: response.error });
  };

  const extract = async (id: string) => {
    setMessage("Extracting…");
    const response = await api.post<any>(`/api/admin/documents/${id}/extract`);
    if (response.error) {
      setMessage(response.error);
      return;
    }
    const summary = response.data;
    setMessage(
      `${summary.status}: ${summary.figures_found} figure(s), ` +
      `${summary.risk_factors_found} risk factor(s), ` +
      `${summary.commentary_found} commentary quote(s) — all queued for review.`,
    );
    await load();
    await openDocument(id);
  };

  const decide = async (
    citationId: string,
    decision: "approve" | "reject",
    overrideValue?: number,
  ) => {
    const response = await api.post<any>(
      `/api/admin/documents/citations/${citationId}/${decision}`,
      decision === "approve" && overrideValue !== undefined
        ? { override_value: overrideValue }
        : {},
    );
    setMessage(response.error || response.data?.note || `Citation ${decision}d.`);
    await load();
    if (selected?.document?.id) await openDocument(selected.document.id);
  };

  if (loading) return <Panel><Loading /></Panel>;

  return (
    <div className="space-y-3">
      <Panel
        title="Document research"
        subtitle={reviewNote}
        actions={
          <div className="flex gap-1">
            {(["library", "queue"] as const).map((key) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={
                  tab === key
                    ? "chip border-accent/50 bg-accent/10 text-accent"
                    : "chip border-line bg-raised text-ink-muted hover:text-ink"
                }
              >
                {key === "library" ? "Library" : `Review queue (${queue?.count ?? 0})`}
              </button>
            ))}
          </div>
        }
      >
        <Notice tone="info">
          Extraction reads a filing and writes <strong>citations</strong>, not
          facts. Every figure carries its page, the exact line it came from and
          the reasons behind its confidence score. Nothing reaches a research
          page until you approve it.
        </Notice>
      </Panel>

      {message && <Notice tone={message.toLowerCase().includes("error") ? "neg" : "info"}>{message}</Notice>}

      <UploadPanel onUploaded={load} />

      {tab === "library" && (
        <Panel title="Documents" bodyClassName="p-0">
          {documents.length === 0 ? (
            <div className="p-4">
              <Empty message="No documents registered. Upload a filing above, or add one by URL." />
            </div>
          ) : (
            <div className="scroll-x">
              <table className="w-full min-w-[980px]">
                <thead className="border-b border-line">
                  <tr>
                    <th className="th">Title</th>
                    <th className="th">Attached to</th>
                    <th className="th">Type</th>
                    <th className="th">Date</th>
                    <th className="th text-right">Pages</th>
                    <th className="th">Status</th>
                    <th className="th text-right">Citations</th>
                    <th className="th text-right">Pending</th>
                    <th className="th"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {documents.map((document) => (
                    <tr key={document.id} className="hover:bg-raised/40">
                      <td className="td max-w-[240px] truncate text-ink">
                        {document.title}
                        {document.url && (
                          <a
                            href={document.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="ml-1 text-2xs text-accent underline"
                          >
                            source
                          </a>
                        )}
                      </td>
                      <td className="td">
                        {document.symbol ? (
                          <Link href={`/stocks/${document.symbol}`} className="text-accent">
                            {document.symbol}
                          </Link>
                        ) : document.ipo_id ? (
                          <Link href={`/ipo/${document.ipo_id}`} className="text-accent">
                            IPO
                          </Link>
                        ) : DASH}
                      </td>
                      <td className="td text-2xs">{titleCase(document.doc_type)}</td>
                      <td className="td text-2xs">{dateIST(document.document_date)}</td>
                      <td className="td num text-right text-ink-muted">
                        {document.page_count ?? DASH}
                      </td>
                      <td className="td">
                        <Tag
                          tone={
                            document.extraction_status === "EXTRACTED" ? "pos"
                              : document.extraction_status === "FAILED" ? "neg"
                                : "neutral"
                          }
                        >
                          {titleCase(document.extraction_status)}
                        </Tag>
                      </td>
                      <td className="td num text-right">{document.citations}</td>
                      <td className="td num text-right text-warn">
                        {document.pending_review}
                      </td>
                      <td className="td">
                        <div className="flex gap-1">
                          <button className="btn px-2 py-0.5" onClick={() => extract(document.id)}>
                            Extract
                          </button>
                          <button className="btn px-2 py-0.5" onClick={() => openDocument(document.id)}>
                            Open
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}

      {tab === "queue" && queue && (
        <Panel
          title={`Review queue — ${queue.count} awaiting a decision`}
          subtitle={queue.guidance}
        >
          {queue.citations.length === 0 ? (
            <Empty message="Nothing awaiting review." />
          ) : (
            <ul className="space-y-2">
              {queue.citations.map((citation: any) => (
                <CitationRow
                  key={citation.id}
                  citation={citation}
                  threshold={queue.auto_accept_threshold}
                  onDecide={decide}
                />
              ))}
            </ul>
          )}
        </Panel>
      )}

      {selected && !selected.error && (
        <DocumentDetail
          detail={selected}
          onDecide={decide}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */

function UploadPanel({ onUploaded }: { onUploaded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState("ANNUAL_REPORT");
  const [symbol, setSymbol] = useState("");
  const [title, setTitle] = useState("");
  const [documentDate, setDocumentDate] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = async () => {
    setError(null);
    setBusy(true);

    if (file) {
      // multipart, so the shared JSON client is bypassed deliberately
      const form = new FormData();
      form.append("file", file);
      form.append("doc_type", docType);
      form.append("title", title);
      if (symbol) form.append("symbol", symbol.toUpperCase());
      if (documentDate) form.append("document_date", documentDate);

      const response = await fetch("/api/admin/documents/upload", {
        method: "POST",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: form,
      });
      setBusy(false);
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        setError(body.detail || `Upload failed (HTTP ${response.status})`);
        return;
      }
      setFile(null);
      setTitle("");
      onUploaded();
      return;
    }

    const response = await api.post("/api/admin/documents", {
      doc_type: docType,
      title,
      symbol: symbol ? symbol.toUpperCase() : null,
      url,
      document_date: documentDate || null,
    });
    setBusy(false);
    if (response.error) setError(response.error);
    else {
      setUrl("");
      setTitle("");
      onUploaded();
    }
  };

  return (
    <Panel
      title="Add a filing"
      subtitle="Upload a PDF/HTML/text file, or register one by URL"
    >
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <label className="block">
          <span className="text-2xs text-ink-muted">Document type</span>
          <select
            value={docType}
            onChange={(e) => setDocType(e.target.value)}
            className="field mt-0.5"
          >
            {DOC_TYPES.map((type) => (
              <option key={type} value={type}>{titleCase(type)}</option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-2xs text-ink-muted">Symbol</span>
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="field mt-0.5"
            placeholder="e.g. HDFCBANK"
          />
        </label>
        <label className="block">
          <span className="text-2xs text-ink-muted">Title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} className="field mt-0.5" />
        </label>
        <label className="block">
          <span className="text-2xs text-ink-muted">Document date</span>
          <input
            type="date"
            value={documentDate}
            onChange={(e) => setDocumentDate(e.target.value)}
            className="field mt-0.5"
          />
        </label>
        <label className="block sm:col-span-2">
          <span className="text-2xs text-ink-muted">File (PDF, HTML or text)</span>
          <input
            type="file"
            accept=".pdf,.html,.htm,.txt,.md"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="field mt-0.5 file:mr-2 file:rounded file:border-0 file:bg-raised file:px-2 file:py-0.5 file:text-ink"
          />
        </label>
        <label className="block sm:col-span-2">
          <span className="text-2xs text-ink-muted">…or a URL instead</span>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="field mt-0.5"
            placeholder="https://…/annual-report.pdf"
            disabled={Boolean(file)}
          />
        </label>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button
          className="btn btn-accent"
          onClick={upload}
          disabled={busy || (!file && !url) || !symbol}
        >
          {busy ? "Saving…" : "Add document"}
        </button>
        <span className="text-2xs text-ink-muted">
          Scanned PDFs cannot be read — this pipeline does not perform OCR, and
          will tell you when a document is image-only.
        </span>
      </div>
      {error && <Notice tone="neg">{error}</Notice>}
    </Panel>
  );
}

/* ------------------------------------------------------------------ */

function CitationRow({
  citation,
  threshold,
  onDecide,
}: {
  citation: any;
  threshold: number;
  onDecide: (id: string, decision: "approve" | "reject", value?: number) => void;
  }) {
  const [override, setOverride] = useState<string>("");
  const confident = (citation.confidence ?? 0) >= threshold;

  return (
    <li className="rounded border border-line bg-raised/30 p-2.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <Tag tone={citation.type === "FIGURE" ? "accent" : "neutral"}>
              {titleCase(citation.type)}
            </Tag>
            <span className="text-xs font-medium text-ink">{citation.claim}</span>
            {citation.period_label && <Tag tone="neutral">{citation.period_label}</Tag>}
            <Tag tone={confident ? "pos" : "warn"}>
              confidence {num(citation.confidence, 2)}
            </Tag>
            {citation.review_status !== "PENDING" && (
              <Tag tone={citation.review_status === "APPROVED" ? "pos" : "neg"}>
                {citation.review_status}
              </Tag>
            )}
          </div>

          <p className="num mt-1 rounded bg-bg px-2 py-1 text-2xs text-ink-dim">
            “{citation.quote}”
          </p>

          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-2xs text-ink-muted">
            <span>
              {citation.source.title || citation.source.doc_type}
              {citation.page ? `, page ${citation.page}` : ""}
            </span>
            {citation.section && <span>section {titleCase(citation.section)}</span>}
            {citation.source.symbol && <span>{citation.source.symbol}</span>}
            {citation.source.url && (
              <a
                href={citation.source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent underline"
              >
                open source
              </a>
            )}
          </div>

          {citation.type === "FIGURE" && (
            <dl className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 text-2xs sm:grid-cols-4">
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">As printed</dt>
                <dd className="num text-ink">{num(citation.raw_value, 2)}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">Unit</dt>
                <dd className="text-ink">{citation.unit || "not established"}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">Multiplier</dt>
                <dd className="num text-ink">
                  {citation.unit_multiplier ? `×${citation.unit_multiplier.toLocaleString()}` : DASH}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">Normalised</dt>
                <dd className="num text-accent">
                  {citation.normalised_value !== null
                    ? compactInr(citation.normalised_value)
                    : "not normalised"}
                </dd>
              </div>
            </dl>
          )}

          <Disclosure summary="Why this confidence?" count={citation.confidence_reasons?.length}>
            <ul className="list-inside list-disc space-y-0.5 text-2xs text-ink-muted">
              {(citation.confidence_reasons || []).map((reason: string, i: number) => (
                <li key={i}>{reason}</li>
              ))}
            </ul>
          </Disclosure>
        </div>

        {citation.review_status === "PENDING" && (
          <div className="flex shrink-0 items-center gap-1">
            {citation.type === "FIGURE" && (
              <input
                value={override}
                onChange={(e) => setOverride(e.target.value)}
                placeholder="correct value"
                className="field w-28 py-0.5"
                aria-label="Corrected value"
              />
            )}
            <button
              className="btn btn-accent px-2 py-0.5"
              onClick={() =>
                onDecide(citation.id, "approve",
                  override ? Number(override) : undefined)
              }
            >
              Approve
            </button>
            <button
              className="btn px-2 py-0.5"
              onClick={() => onDecide(citation.id, "reject")}
            >
              Reject
            </button>
          </div>
        )}
      </div>

      {citation.normalised_value === null && citation.type === "FIGURE" && (
        <p className="mt-1.5 text-2xs text-warn">
          No unit declaration was found near this figure, so it has not been
          normalised. Indian filings print in lakhs or crore — approving without
          checking risks a 100× error.
        </p>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------ */

function DocumentDetail({
  detail,
  onDecide,
  onClose,
}: {
  detail: any;
  onDecide: (id: string, decision: "approve" | "reject", value?: number) => void;
  onClose: () => void;
}) {
  const groups = detail.citations || {};
  const note = detail.document.extraction_note;

  return (
    <Panel
      title={detail.document.title}
      subtitle={`${titleCase(detail.document.doc_type)} · ${dateIST(detail.document.document_date)} · ${detail.document.page_count ?? "?"} pages`}
      actions={<button className="btn" onClick={onClose}>Close</button>}
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="Citations" value={detail.summary.total} />
        <Stat label="Pending" value={detail.summary.pending} tone="warn" />
        <Stat label="Approved" value={detail.summary.approved} tone="pos" />
        <Stat label="Rejected" value={detail.summary.rejected} tone="muted" />
        <Stat
          label="High confidence"
          value={`${detail.summary.high_confidence} (≥ ${detail.summary.auto_accept_threshold})`}
        />
      </div>

      {note?.warnings?.length > 0 && (
        <div className="mt-3 space-y-1.5">
          {note.warnings.map((warning: string) => (
            <Notice key={warning} tone="warn">{warning}</Notice>
          ))}
        </div>
      )}

      <div className="mt-3 space-y-4">
        {Object.entries(groups).map(([type, citations]: [string, any]) => (
          <div key={type}>
            <h3 className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-muted">
              {titleCase(type)} · {citations.length}
            </h3>
            <ul className="space-y-2">
              {citations.map((citation: any) => (
                <CitationRow
                  key={citation.id}
                  citation={citation}
                  threshold={detail.summary.auto_accept_threshold}
                  onDecide={onDecide}
                />
              ))}
            </ul>
          </div>
        ))}
        {Object.keys(groups).length === 0 && (
          <Empty message="No citations yet — run extraction on this document." />
        )}
      </div>
    </Panel>
  );
}
