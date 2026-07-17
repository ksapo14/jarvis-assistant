import { Icon, type IconName } from "./Icon";

export type PageId = "assistant" | "history" | "tools" | "voice" | "providers" | "general";

const navigation: Array<{ id: PageId; label: string; icon: IconName }> = [
  { id: "assistant", label: "Assistant", icon: "assistant" },
  { id: "history", label: "Activity", icon: "history" },
  { id: "tools", label: "Tools", icon: "tools" },
  { id: "voice", label: "Voice", icon: "voice" },
  { id: "providers", label: "Providers", icon: "providers" },
  { id: "general", label: "Settings", icon: "settings" },
];

interface SidebarProps {
  page: PageId;
  onNavigate: (page: PageId) => void;
  connected: boolean;
}

export function Sidebar({ page, onNavigate, connected }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Primary navigation">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          <span />
        </span>
        <div>
          <strong>JARVIS</strong>
          <small>desktop intelligence</small>
        </div>
      </div>
      <nav className="nav-list">
        {navigation.map((item) => (
          <button
            className={page === item.id ? "nav-item active" : "nav-item"}
            key={item.id}
            onClick={() => onNavigate(item.id)}
            aria-current={page === item.id ? "page" : undefined}
          >
            <Icon name={item.icon} />
            <span>{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar-footer">
        <span className={connected ? "connection-dot online" : "connection-dot"} />
        <div>
          <strong>{connected ? "Local backend" : "Reconnecting"}</strong>
          <small>{connected ? "Authenticated · localhost" : "No desktop control available"}</small>
        </div>
      </div>
    </aside>
  );
}
