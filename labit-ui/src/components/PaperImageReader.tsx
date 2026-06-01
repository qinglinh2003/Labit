import { useEffect, useState, useRef, useCallback } from "react";
import { ZoomIn, ZoomOut, RotateCcw } from "lucide-react";
import {
  fetchReaderManifest,
  pageImageUrl,
  PaperRecord,
  ReaderManifest,
  renderUrl,
} from "../api/client";

interface PageEntry {
  page: number;
  src: string;
  cssWidth: number;
  cssHeight: number;
  firstTileSrc?: string;
  firstTileCssHeight?: number;
  loaded: boolean;
}

export default function PaperImageReader({
  project,
  paper,
}: {
  project: string;
  paper: PaperRecord;
}) {
  const [manifest, setManifest] = useState<ReaderManifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pages, setPages] = useState<PageEntry[]>([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(1.0);
  const containerRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const gestureScaleRef = useRef(1);

  const clampZoom = useCallback((z: number) => Math.min(Math.max(z, 0.5), 3.0), []);
  const zoomIn = useCallback(() => setZoom((z) => clampZoom(z + 0.25)), [clampZoom]);
  const zoomOut = useCallback(() => setZoom((z) => clampZoom(z - 0.25)), [clampZoom]);
  const zoomReset = useCallback(() => setZoom(1.0), []);

  // Trackpad pinch-to-zoom. Chrome/Firefox emit ctrl+wheel; Safari emits gesture events.
  useEffect(() => {
    const eventIsInsideReader = (event: Event) => {
      const container = containerRef.current;
      return !!container && event.target instanceof Node && container.contains(event.target);
    };

    const handleWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      if (!eventIsInsideReader(e)) return;
      e.preventDefault();
      e.stopPropagation();
      setZoom((z) => clampZoom(z - e.deltaY * 0.005));
    };

    const handleGestureStart = (e: Event) => {
      if (!eventIsInsideReader(e)) return;
      e.preventDefault();
      gestureScaleRef.current = 1;
    };

    const handleGestureChange = (e: Event) => {
      if (!eventIsInsideReader(e)) return;
      e.preventDefault();
      e.stopPropagation();
      const scale = "scale" in e && typeof e.scale === "number" ? e.scale : 1;
      const delta = scale - gestureScaleRef.current;
      gestureScaleRef.current = scale;
      setZoom((z) => clampZoom(z + delta));
    };

    window.addEventListener("wheel", handleWheel, { passive: false, capture: true });
    window.addEventListener("gesturestart", handleGestureStart, { passive: false, capture: true });
    window.addEventListener("gesturechange", handleGestureChange, { passive: false, capture: true });
    return () => {
      window.removeEventListener("wheel", handleWheel, { capture: true });
      window.removeEventListener("gesturestart", handleGestureStart, { capture: true });
      window.removeEventListener("gesturechange", handleGestureChange, { capture: true });
    };
  }, [clampZoom, manifest]);

  // Fetch manifest when paper changes
  useEffect(() => {
    setManifest(null);
    setError(null);
    setPages([]);

    let canceled = false;
    fetchReaderManifest(project, paper.id)
      .then((m) => {
        if (!canceled) setManifest(m);
      })
      .catch((err: Error) => {
        if (!canceled) setError(err.message || "Failed to load reader manifest");
      });
    return () => {
      canceled = true;
    };
  }, [project, paper.id]);

  // Build page list from manifest
  useEffect(() => {
    if (!manifest) return;

    const p1 = manifest.pages[0];
    if (!p1) return;

    // Start page 1 with the pre-rendered viewport tile. The full page image is
    // swapped in only after the tile is visible, keeping the click path tiny.
    const initialPages: PageEntry[] = [
      {
        page: 1,
        src: renderUrl(project, paper.id, p1.retina),
        firstTileSrc: renderUrl(project, paper.id, p1.first_viewport_tile),
        cssWidth: p1.css_width,
        cssHeight: p1.css_height,
        firstTileCssHeight: p1.first_tile_css_height,
        loaded: false,
      },
    ];

    // Add remaining pages (will be loaded on-demand via scroll)
    for (let i = 2; i <= manifest.page_count; i++) {
      initialPages.push({
        page: i,
        src: pageImageUrl(project, paper.id, i, 1600),
        cssWidth: p1.css_width,
        cssHeight: p1.css_height, // approximate — same aspect ratio
        loaded: false,
      });
    }

    setPages(initialPages);
  }, [manifest, project, paper.id]);

  const markLoaded = useCallback((pageNum: number) => {
    setPages((prev) =>
      prev.map((p) => (p.page === pageNum ? { ...p, loaded: true } : p))
    );
  }, []);

  const registerPageRef = useCallback((page: number, el: HTMLDivElement | null) => {
    if (el) {
      pageRefs.current.set(page, el);
    } else {
      pageRefs.current.delete(page);
    }
  }, []);

  // Track which page is currently most visible in the viewport
  useEffect(() => {
    if (pages.length === 0) return;
    const container = containerRef.current;
    if (!container) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // Find the entry with the highest intersection ratio
        let best: { page: number; ratio: number } | null = null;
        for (const entry of entries) {
          const pageNum = Number(entry.target.getAttribute("data-page"));
          if (!pageNum) continue;
          if (!best || entry.intersectionRatio > best.ratio) {
            best = { page: pageNum, ratio: entry.intersectionRatio };
          }
        }
        if (best && best.ratio > 0) {
          setCurrentPage(best.page);
        }
      },
      { root: container, threshold: [0, 0.25, 0.5, 0.75, 1.0] }
    );

    for (const [, el] of pageRefs.current) {
      observer.observe(el);
    }

    return () => observer.disconnect();
  }, [pages]);

  if (error) {
    return (
      <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {error}
      </div>
    );
  }

  if (!manifest || pages.length === 0) {
    return <ReaderSkeleton />;
  }

  return (
    <div className="relative">
      <div
        ref={containerRef}
        className="overflow-auto rounded-md border border-slate-200 bg-slate-100"
        style={{ height: "80vh", overscrollBehavior: "contain" }}
      >
        <div className="flex flex-col items-center gap-2 py-4">
          {pages.map((entry) => (
            <PageImage
              key={entry.page}
              entry={entry}
              zoom={zoom}
              containerRef={containerRef}
              onLoad={() => markLoaded(entry.page)}
              registerRef={registerPageRef}
            />
          ))}
        </div>
      </div>
      <div className="absolute bottom-4 right-4 flex items-center gap-1 rounded bg-black/60 px-1.5 py-1 text-xs text-white shadow">
        <button onClick={zoomOut} className="p-0.5 hover:text-slate-300" title="Zoom out">
          <ZoomOut size={14} />
        </button>
        <span className="min-w-[3ch] text-center">{Math.round(zoom * 100)}%</span>
        <button onClick={zoomIn} className="p-0.5 hover:text-slate-300" title="Zoom in">
          <ZoomIn size={14} />
        </button>
        <button onClick={zoomReset} className="p-0.5 hover:text-slate-300" title="Reset zoom">
          <RotateCcw size={12} />
        </button>
        {manifest && manifest.page_count > 1 && (
          <span className="ml-1 border-l border-white/30 pl-1.5">
            {currentPage} / {manifest.page_count}
          </span>
        )}
      </div>
    </div>
  );
}

function PageImage({
  entry,
  zoom,
  containerRef,
  onLoad,
  registerRef,
}: {
  entry: PageEntry;
  zoom: number;
  containerRef: React.RefObject<HTMLDivElement | null>;
  onLoad: () => void;
  registerRef: (page: number, el: HTMLDivElement | null) => void;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [visible, setVisible] = useState(entry.page === 1);
  const [showFullPage, setShowFullPage] = useState(!entry.firstTileSrc);
  const [imgError, setImgError] = useState(false);
  const activeSrc = entry.firstTileSrc && !showFullPage ? entry.firstTileSrc : entry.src;
  const activeHeight = entry.firstTileSrc && !showFullPage
    ? entry.firstTileCssHeight ?? entry.cssHeight
    : entry.cssHeight;

  // Intersection observer for lazy loading
  useEffect(() => {
    if (visible) return;
    const el = imgRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { root: containerRef.current, rootMargin: "600px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [visible, containerRef]);

  const divRefCallback = useCallback(
    (el: HTMLDivElement | null) => registerRef(entry.page, el),
    [entry.page, registerRef]
  );

  return (
    <div
      ref={divRefCallback}
      data-page={entry.page}
      className="bg-white shadow-sm"
      style={{
        width: entry.cssWidth * zoom,
        minHeight: entry.cssHeight * zoom,
      }}
    >
      {imgError ? (
        <div className="flex items-center justify-center p-8 text-sm text-slate-400">
          Page {entry.page} failed to load
        </div>
      ) : (
        <img
          ref={imgRef}
          src={visible ? activeSrc : undefined}
          width={entry.cssWidth * zoom}
          height={activeHeight * zoom}
          decoding="async"
          fetchPriority={entry.page === 1 ? "high" : "auto"}
          loading={entry.page === 1 ? "eager" : "lazy"}
          onLoad={() => {
            onLoad();
            if (entry.firstTileSrc && !showFullPage) {
              requestIdle(() => setShowFullPage(true));
            }
          }}
          onError={() => setImgError(true)}
          style={{ display: "block", width: "100%", height: "auto" }}
          alt={`Page ${entry.page}`}
        />
      )}
    </div>
  );
}

function requestIdle(callback: () => void) {
  if ("requestIdleCallback" in window) {
    window.requestIdleCallback(callback, { timeout: 1500 });
    return;
  }
  globalThis.setTimeout(callback, 500);
}

function ReaderSkeleton() {
  return (
    <div className="flex h-[80vh] items-center justify-center rounded-md border border-slate-200 bg-slate-100">
      <div className="w-full max-w-3xl rounded-md border border-slate-200 bg-white p-5 shadow-sm">
        <div className="h-3 w-28 rounded bg-slate-200" />
        <div className="mt-4 h-3 w-full rounded bg-slate-100" />
        <div className="mt-2 h-3 w-5/6 rounded bg-slate-100" />
        <div className="mt-5 text-sm text-slate-500">Loading reader...</div>
      </div>
    </div>
  );
}
