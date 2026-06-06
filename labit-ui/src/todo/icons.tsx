// icons.tsx — stroked SVG icon set for the Todo module.
import React from "react";

type P = Omit<React.SVGProps<SVGSVGElement>, "stroke"> & { size?: number; stroke?: number; className?: string };
const make = (path: React.ReactNode, vb = "0 0 24 24") =>
  function Icon({ size = 16, stroke = 2, className, ...rest }: P) {
    return (
      <svg width={size} height={size} viewBox={vb} fill="none" stroke="currentColor"
        strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" aria-hidden className={className} {...rest}>
        {path}
      </svg>
    );
  };

export const IconBook = make(<><path d="M3 5a2 2 0 0 1 2-2h6v17H5a2 2 0 0 0-2 2z" /><path d="M21 5a2 2 0 0 0-2-2h-6v17h6a2 2 0 0 1 2 2z" /></>);
export const IconPaper = make(<><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h4" /></>);
export const IconCheckSquare = make(<><path d="M9 11l3 3 8-8" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></>);
export const IconFlask = make(<><path d="M9 3h6M10 3v6l-5.5 9A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-3L14 9V3" /><path d="M7.5 15h9" /></>);
export const IconTerminal = make(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 9l3 3-3 3M13 15h4" /></>);
export const IconCapture = make(<><path d="M12 3v4M12 17v4M3 12h4M17 12h4" /><circle cx="12" cy="12" r="4" /></>);
export const IconChevronLeft = make(<path d="M15 18l-6-6 6-6" />);
export const IconChevronRight = make(<path d="M9 18l6-6-6-6" />);
export const IconChevronDown = make(<path d="M6 9l6 6 6-6" />);
export const IconPlus = make(<path d="M12 5v14M5 12h14" />);
export const IconRefresh = make(<><path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 4v5h-5" /></>);
export const IconSearch = make(<><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" /></>);
export const IconTrash = make(<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13" />);
export const IconArrowRight = make(<path d="M5 12h13M13 6l6 6-6 6" />);
export const IconLink = make(<><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1" /><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1" /></>);
export const IconCheck = make(<path d="M5 12l4.5 4.5L19 7" />);
export const IconChat = make(<><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></>);
export const IconArchive = make(<><rect x="3" y="4" width="18" height="4" rx="1" /><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4" /></>);
export const IconCarry = make(<><path d="M3 12h13" /><path d="M11 6l6 6-6 6" /><path d="M21 5v14" /></>);
export const IconX = make(<path d="M6 6l12 12M18 6L6 18" />);
export const IconCloud = make(<><path d="M7 18a4 4 0 0 1 0-8 5 5 0 0 1 9.6-1.3A3.5 3.5 0 0 1 17.5 18z" /><path d="M9.5 14.5l2 2 3.5-3.5" /></>);
export const IconDrag = make(<>{[6, 12, 18].flatMap((cy) => [9, 15].map((cx) => <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r="1" fill="currentColor" stroke="none" />))}</>);
