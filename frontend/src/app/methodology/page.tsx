import { Disclosure, Panel, Tag, Unavailable } from "@/components/primitives";
import { apiFetch } from "@/lib/api";
import { titleCase } from "@/lib/format";

export const dynamic = "force-dynamic";

/**
 * Requirement: no hidden model. This page serves the methodology documents
 * straight from the backend and names the exact source file behind every
 * engine, so a reader can go and check the arithmetic.
 */
export default async function MethodologyPage() {
  const result = await apiFetch<any>("/api/methodology", { auth: false });
  if (!result.data) {
    return <Panel title="Methodology"><Unavailable reason={result.error} /></Panel>;
  }
  const { documents, engine_versions, source_files, note } = result.data;

  return (
    <div className="space-y-3">
      <Panel title="Methodology" subtitle={note}>
        <div className="grid gap-3 lg:grid-cols-2">
          <div>
            <h3 className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-muted">
              Engine versions
            </h3>
            <ul className="space-y-1">
              {Object.entries(engine_versions).map(([key, version]) => (
                <li key={key} className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-ink-dim">{titleCase(key)}</span>
                  <span className="num text-ink-muted">{String(version)}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="mb-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-muted">
              Where each formula lives
            </h3>
            <ul className="space-y-1">
              {Object.entries(source_files).map(([key, path]) => (
                <li key={key} className="flex items-center justify-between gap-2 text-xs">
                  <span className="text-ink-dim">{titleCase(key)}</span>
                  <code className="num text-2xs text-accent">{String(path)}</code>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Panel>

      {documents.length === 0 ? (
        <Panel>
          <Unavailable reason="No methodology documents were found in backend/docs/methodology." />
        </Panel>
      ) : (
        documents.map((document: any) => (
          <Panel key={document.slug} title={document.title}>
            <article
              id={document.slug}
              className="prose-sm max-w-none space-y-2 text-xs leading-relaxed text-ink-dim"
            >
              <Markdown content={document.content} />
            </article>
          </Panel>
        ))
      )}
    </div>
  );
}

/**
 * A deliberately tiny markdown renderer: headings, lists, code fences, tables
 * and paragraphs. Pulling in a full markdown pipeline (and a sanitiser) for
 * four files we author ourselves is not a dependency worth carrying.
 */
function Markdown({ content }: { content: string }) {
  const blocks = content.split(/\n{2,}/);
  return (
    <>
      {blocks.map((block, index) => {
        const trimmed = block.trim();
        if (!trimmed) return null;

        if (trimmed.startsWith("```")) {
          const body = trimmed.replace(/^```[a-z]*\n?/, "").replace(/```$/, "");
          return (
            <pre
              key={index}
              className="num overflow-x-auto rounded border border-line bg-bg p-2.5 text-2xs text-ink-dim"
            >
              {body}
            </pre>
          );
        }

        const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed);
        if (heading) {
          const level = heading[1].length;
          const text = heading[2];
          const Tag = (level <= 2 ? "h3" : "h4") as "h3" | "h4";
          return (
            <Tag
              key={index}
              className={
                level <= 2
                  ? "mt-4 text-sm font-semibold text-ink"
                  : "mt-3 text-xs font-semibold uppercase tracking-wide text-ink-dim"
              }
            >
              {text}
            </Tag>
          );
        }

        if (trimmed.startsWith("|")) {
          const rows = trimmed.split("\n").filter((r) => !/^\|[\s:|-]+\|$/.test(r));
          const cells = rows.map((row) =>
            row.split("|").slice(1, -1).map((cell) => cell.trim()),
          );
          const [header, ...body] = cells;
          return (
            <div key={index} className="scroll-x">
              <table className="w-full min-w-[420px]">
                <thead>
                  <tr>
                    {header.map((cell, i) => (
                      <th key={i} className="th">{cell}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {body.map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j} className="td whitespace-normal text-ink-dim">
                          <Inline text={cell} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }

        if (/^[-*]\s+/m.test(trimmed)) {
          const items = trimmed.split("\n").filter((line) => /^[-*]\s+/.test(line));
          return (
            <ul key={index} className="list-inside list-disc space-y-1">
              {items.map((item, i) => (
                <li key={i}>
                  <Inline text={item.replace(/^[-*]\s+/, "")} />
                </li>
              ))}
            </ul>
          );
        }

        if (/^\d+\.\s+/m.test(trimmed)) {
          const items = trimmed.split("\n").filter((line) => /^\d+\.\s+/.test(line));
          return (
            <ol key={index} className="list-inside list-decimal space-y-1">
              {items.map((item, i) => (
                <li key={i}>
                  <Inline text={item.replace(/^\d+\.\s+/, "")} />
                </li>
              ))}
            </ol>
          );
        }

        return (
          <p key={index}>
            <Inline text={trimmed} />
          </p>
        );
      })}
    </>
  );
}

/** Handles `code` and **bold** only. Everything else renders as plain text. */
function Inline({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith("`") && part.endsWith("`")) {
          return (
            <code key={index} className="num rounded bg-bg px-1 py-0.5 text-accent">
              {part.slice(1, -1)}
            </code>
          );
        }
        if (part.startsWith("**") && part.endsWith("**")) {
          return (
            <strong key={index} className="font-semibold text-ink">
              {part.slice(2, -2)}
            </strong>
          );
        }
        return <span key={index}>{part}</span>;
      })}
    </>
  );
}
