import type { SVGProps } from "react";

export type IconName =
  | "assistant"
  | "history"
  | "tools"
  | "voice"
  | "providers"
  | "settings"
  | "microphone"
  | "stop"
  | "shield"
  | "check"
  | "x"
  | "refresh"
  | "activity"
  | "volume";

const paths: Record<IconName, React.ReactNode> = {
  assistant: (
    <>
      <circle cx="12" cy="12" r="3.25" />
      <path d="M4.2 13.8a8 8 0 0 1 6-9.55M13.8 4.2a8 8 0 0 1 5.95 6M19.8 13.8a8 8 0 0 1-6 5.95M10.2 19.8a8 8 0 0 1-5.95-6" />
    </>
  ),
  history: (
    <>
      <path d="M4 12a8 8 0 1 0 2.34-5.66L4 8.68" />
      <path d="M4 4v4.68h4.68M12 7.5V12l3.2 1.9" />
    </>
  ),
  tools: (
    <>
      <path d="m14.7 6.3 3-3a4.2 4.2 0 0 1-5.3 5.3l-7.7 7.7a2.12 2.12 0 0 0 3 3l7.7-7.7a4.2 4.2 0 0 0 5.3-5.3l-3 3Z" />
    </>
  ),
  voice: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 10.5a6.5 6.5 0 0 0 13 0M12 17v4M9 21h6" />
    </>
  ),
  providers: (
    <>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="m8.2 7.3 2.7 8.2M15.8 7.3l-2.7 8.2M8.5 6h7" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21h-4v-.09A1.7 1.7 0 0 0 9 19.35a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.63 15a1.7 1.7 0 0 0-1.56-1.03H3v-4h.09A1.7 1.7 0 0 0 4.65 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.63h.02A1.7 1.7 0 0 0 10.05 3.1V3h4v.09A1.7 1.7 0 0 0 15.08 4.65a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.45 9v.02A1.7 1.7 0 0 0 21 10.05h.1v4H21A1.7 1.7 0 0 0 19.4 15Z" />
    </>
  ),
  microphone: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 10.5a6.5 6.5 0 0 0 13 0M12 17v4M9 21h6" />
    </>
  ),
  stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
  shield: <path d="M12 3 5 6v5c0 4.6 2.95 8.35 7 10 4.05-1.65 7-5.4 7-10V6l-7-3Zm-3 9 2 2 4-4" />,
  check: <path d="m5 12 4 4L19 6" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  refresh: <path d="M20 11a8 8 0 1 0-2.34 5.66L20 14.32M20 20v-5.68h-5.68" />,
  activity: <path d="M3 12h4l2.2-6 4.1 12 2.2-6H21" />,
  volume: (
    <>
      <path d="M5 10v4h3l4 3V7L8 10H5Z" />
      <path d="M15 9.5a4 4 0 0 1 0 5M17.5 7a7.5 7.5 0 0 1 0 10" />
    </>
  ),
};

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}
