"use client";

import clsx from "clsx";
import {
  Activity, AlertTriangle, Bell, BookOpen, Briefcase, CalendarDays,
  ChartCandlestick, FileText, FlaskConical, Gauge, Home, Landmark, Layers,
  LineChart, Menu, Newspaper, Radar, ScrollText, Search, Settings, Shield,
  Star, User, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { SearchDialog } from "@/components/SearchDialog";
import { Tag } from "@/components/primitives";
import { api, getToken } from "@/lib/api";
import { num, pct, signClass } from "@/lib/format";

const NAV = [
  { href: "/", label: "Dashboard", icon: Home },
  { href: "/stocks", label: "Stocks", icon: LineChart },
  { href: "/indices", label: "Indices", icon: Landmark },
  { href: "/fno", label: "F&O", icon: Layers },
  { href: "/fno/options", label: "Option chain", icon: ChartCandlestick },
  { href: "/fno/futures", label: "Futures", icon: Activity },
  { href: "/ipo", label: "IPO", icon: Gauge },
  { href: "/news", label: "News", icon: Newspaper },
  { href: "/research", label: "Research", icon: FileText },
  { href: "/documents", label: "Documents", icon: ScrollText },
  { href: "/scanners", label: "Scanners", icon: Radar },
  { href: "/backtesting", label: "Backtesting", icon: FlaskConical },
  { href: "/watchlist", label: "Watchlist", icon: Star },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/calendar", label: "Calendar", icon: CalendarDays },
  { href: "/methodology", label: "Methodology", icon: BookOpen },
  { href: "/compliance", label: "Compliance", icon: Shield },
  { href: "/settings", label: "Settings", icon: Settings },
];

const MOBILE_NAV = [
  { href: "/", label: "Home", icon: Home },
  { href: "/stocks", label: "Stocks", icon: LineChart },
  { href: "/watchlist", label: "Watch", icon: Star },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/settings", label: "Profile", icon: User },
];

interface Branding {
  name: string;
  short_name: string;
  tagline: string;
  logo_mark_text: string;
  footer_text: string;
}

interface ComplianceSnapshot {
  descriptor: string;
  is_registered: boolean;
  verification_badge: { label: string; number: string; caveat: string } | null;
  disclaimers: { primary: string; derivatives: string };
  review_overdue: boolean;
}

export function Shell({
  branding,
  compliance,
  children,
}: {
  branding: Branding;
  compliance: ComplianceSnapshot;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => setNavOpen(false), [pathname]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
      if (event.key === "Escape") setSearchOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-accent focus:px-3 focus:py-1.5 focus:text-bg"
      >
        Skip to content
      </a>

      <TopBar
        branding={branding}
        compliance={compliance}
        onMenu={() => setNavOpen((v) => !v)}
        onSearch={() => setSearchOpen(true)}
      />

      <ComplianceBanner compliance={compliance} />

      <div className="mx-auto flex w-full max-w-[1800px] gap-0">
        <nav
          aria-label="Primary"
          className={clsx(
            "fixed inset-y-0 left-0 z-40 w-56 shrink-0 overflow-y-auto border-r border-line bg-surface pt-2 transition-transform lg:sticky lg:top-[88px] lg:z-auto lg:h-[calc(100vh-88px)] lg:translate-x-0",
            navOpen ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <div className="flex items-center justify-between px-3 py-2 lg:hidden">
            <span className="text-2xs uppercase tracking-wider text-ink-muted">
              Navigation
            </span>
            <button className="btn px-2 py-1" onClick={() => setNavOpen(false)}>
              <X className="h-3.5 w-3.5" aria-hidden />
              <span className="sr-only">Close navigation</span>
            </button>
          </div>
          <ul className="space-y-0.5 px-2 pb-24">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active =
                href === "/" ? pathname === "/" : pathname.startsWith(href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={active ? "page" : undefined}
                    className={clsx(
                      "flex items-center gap-2.5 rounded px-2.5 py-1.5 text-xs transition-colors",
                      active
                        ? "bg-accent/10 font-semibold text-accent"
                        : "text-ink-dim hover:bg-raised hover:text-ink",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        {navOpen && (
          <div
            className="fixed inset-0 z-30 bg-black/60 lg:hidden"
            onClick={() => setNavOpen(false)}
            aria-hidden
          />
        )}

        <main id="main" className="min-w-0 flex-1 px-3 pb-24 pt-3 lg:px-4 lg:pb-8">
          {children}
          <Footer branding={branding} compliance={compliance} />
        </main>
      </div>

      <nav
        aria-label="Primary mobile"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-surface lg:hidden"
      >
        {MOBILE_NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex flex-col items-center gap-0.5 py-2 text-[10px]",
                active ? "text-accent" : "text-ink-muted",
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {label}
            </Link>
          );
        })}
      </nav>

      {searchOpen && <SearchDialog onClose={() => setSearchOpen(false)} />}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Top bar
 * ---------------------------------------------------------------------- */

interface IndexTick {
  symbol: string;
  ltp: number | null;
  change_pct: number | null;
}

function TopBar({
  branding,
  compliance,
  onMenu,
  onSearch,
}: {
  branding: Branding;
  compliance: ComplianceSnapshot;
  onMenu: () => void;
  onSearch: () => void;
}) {
  const [ticks, setTicks] = useState<IndexTick[]>([]);
  const [status, setStatus] = useState<string>("");
  const [clock, setClock] = useState("");
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const [indices, market] = await Promise.all([
        api.get<{ available: boolean; rows: IndexTick[] }>("/api/indices", false),
        api.get<{ status: string; current_time_ist: string }>(
          "/api/market/status",
          false,
        ),
      ]);
      if (cancelled) return;
      if (indices.data?.rows) setTicks(indices.data.rows.slice(0, 4));
      if (market.data) {
        setStatus(market.data.status);
        setClock(market.data.current_time_ist);
      }
      if (getToken()) {
        const events = await api.get<{ unread_count: number }>(
          "/api/alerts/events?unread_only=true&limit=1",
        );
        if (!cancelled && events.data) setUnread(events.data.unread_count || 0);
      }
    };
    load();
    const timer = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-surface/95 backdrop-blur">
      <div className="mx-auto flex w-full max-w-[1800px] items-center gap-3 px-3 py-2">
        <button className="btn px-2 py-1 lg:hidden" onClick={onMenu}>
          <Menu className="h-4 w-4" aria-hidden />
          <span className="sr-only">Toggle navigation</span>
        </button>

        <Link href="/" className="flex shrink-0 items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded bg-accent text-[11px] font-bold text-bg">
            {branding.logo_mark_text}
          </span>
          <span className="hidden min-w-0 sm:block">
            <span className="block truncate text-xs font-bold text-ink">
              {branding.name}
            </span>
            <span className="block truncate text-[10px] text-ink-muted">
              {compliance.descriptor}
            </span>
          </span>
        </Link>

        <div className="scroll-x hidden flex-1 md:block">
          <div className="flex items-center gap-3">
            {ticks.map((tick) => (
              <div key={tick.symbol} className="shrink-0 whitespace-nowrap">
                <span className="text-2xs text-ink-muted">{tick.symbol}</span>{" "}
                <span className="num text-xs font-semibold text-ink">
                  {num(tick.ltp)}
                </span>{" "}
                <span className={clsx("num text-2xs", signClass(tick.change_pct))}>
                  {pct(tick.change_pct)}
                </span>
              </div>
            ))}
            {ticks.length === 0 && (
              <span className="text-2xs text-ink-muted">
                Index feed unavailable
              </span>
            )}
          </div>
        </div>

        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          <span
            className={clsx(
              "chip hidden sm:inline-flex",
              status === "OPEN"
                ? "border-pos/40 bg-pos/10 text-pos"
                : status === "PRE_OPEN"
                  ? "border-warn/40 bg-warn/10 text-warn"
                  : "border-line-strong bg-raised text-ink-dim",
            )}
            title={`Market status. Current IST time: ${clock}`}
          >
            <span
              className={clsx(
                "h-1.5 w-1.5 rounded-full bg-current",
                status === "OPEN" && "animate-pulseSoft",
              )}
              aria-hidden
            />
            {status ? status.replace("_", " ") : "…"}
          </span>

          <span className="num hidden text-2xs text-ink-muted xl:inline">
            {clock} IST
          </span>

          <button className="btn px-2 py-1" onClick={onSearch} title="Search (Ctrl+K)">
            <Search className="h-3.5 w-3.5" aria-hidden />
            <span className="sr-only">Search</span>
          </button>

          <Link href="/alerts" className="btn relative px-2 py-1" title="Alerts">
            <Bell className="h-3.5 w-3.5" aria-hidden />
            {unread > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-neg px-1 text-[9px] font-bold text-white">
                {unread > 9 ? "9+" : unread}
              </span>
            )}
            <span className="sr-only">Alerts</span>
          </Link>

          <Link href="/settings" className="btn px-2 py-1" title="Account">
            <User className="h-3.5 w-3.5" aria-hidden />
            <span className="sr-only">Account</span>
          </Link>
        </div>
      </div>
    </header>
  );
}

/* -------------------------------------------------------------------------
 * Compliance banner - wording comes entirely from the backend config.
 * ---------------------------------------------------------------------- */

function ComplianceBanner({ compliance }: { compliance: ComplianceSnapshot }) {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;

  return (
    <div className="border-b border-warn/25 bg-warn/5">
      <div className="mx-auto flex w-full max-w-[1800px] items-start gap-2 px-3 py-2">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" aria-hidden />
        <div className="min-w-0 flex-1 text-2xs leading-relaxed text-warn/90">
          <span className="font-semibold">
            {compliance.is_registered
              ? compliance.verification_badge?.label
              : compliance.descriptor}
            :
          </span>{" "}
          {compliance.disclaimers.primary}
          {compliance.is_registered && compliance.verification_badge && (
            <span className="ml-1 opacity-80">
              {compliance.verification_badge.caveat}
            </span>
          )}{" "}
          <Link href="/compliance" className="underline underline-offset-2">
            Full disclosure
          </Link>
        </div>
        <button
          className="shrink-0 text-warn/60 hover:text-warn"
          onClick={() => setDismissed(true)}
          aria-label="Dismiss for this session"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
}

function Footer({
  branding,
  compliance,
}: {
  branding: Branding;
  compliance: ComplianceSnapshot;
}) {
  return (
    <footer className="mt-8 border-t border-line pt-4 text-2xs leading-relaxed text-ink-muted">
      <div className="flex flex-wrap items-center gap-2">
        <Tag tone={compliance.is_registered ? "accent" : "neutral"}>
          {compliance.descriptor}
        </Tag>
        {compliance.review_overdue && (
          <Tag tone="warn">Compliance review overdue</Tag>
        )}
      </div>
      <p className="mt-2 max-w-4xl">{branding.footer_text}</p>
      <p className="mt-1 max-w-4xl">{compliance.disclaimers.primary}</p>
      <div className="mt-2 flex flex-wrap gap-3">
        <Link href="/compliance" className="underline underline-offset-2">
          Compliance &amp; disclosures
        </Link>
        <Link href="/methodology" className="underline underline-offset-2">
          Methodology
        </Link>
        <Link href="/settings" className="underline underline-offset-2">
          Data sources
        </Link>
      </div>
    </footer>
  );
}
