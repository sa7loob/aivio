// أيقونات SVG بسيطة (بدون مكتبة خارجية)
const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export const IconInbox = () => (
  <svg {...base}>
    <path d="M4 13h4l2 3h4l2-3h4" />
    <path d="M5 5h14l1 8v6H4v-6z" />
  </svg>
);

export const IconLeads = () => (
  <svg {...base}>
    <path d="M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" />
    <path d="M2 21v-1a6 6 0 0 1 11-3" />
    <path d="M16 14h6M19 11v6" />
  </svg>
);

export const IconTeam = () => (
  <svg {...base}>
    <circle cx="9" cy="8" r="3.5" />
    <path d="M2.5 20a6.5 6.5 0 0 1 13 0" />
    <path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5" />
  </svg>
);

export const IconChannels = () => (
  <svg {...base}>
    <path d="M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.6A8 8 0 1 1 21 12z" />
  </svg>
);

export const IconMenu = () => (
  <svg {...base} width={22} height={22}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </svg>
);

export const IconSend = () => (
  <svg {...base} width={18} height={18} style={{ transform: "scaleX(-1)" }}>
    <path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" />
  </svg>
);

export const IconBack = () => (
  <svg {...base} width={20} height={20}>
    <path d="M9 6l6 6-6 6" />
  </svg>
);
