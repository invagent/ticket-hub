import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HubCollabActions } from "../hubActions";

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const baseHub: any = {
  id: 1,
  short_code: "HUB-1",
  title: "t",
  type: "Bug_fix",
  status: "created",
  linear_identifier: "ENG-1",
  release_notified_at: null,
  self_found: false,
  feedback_status: null,
  last_urged_at: null,
};

beforeEach(() => {
  localStorage.setItem("auth_user", JSON.stringify({ role: "supervisor" }));
});

describe("HubCollabActions", () => {
  it("shows 催办 for dev + not done + has linear", () => {
    wrap(<HubCollabActions hub={baseHub} />);
    expect(screen.getByText("催办")).toBeInTheDocument();
  });

  it("hides all for non-supervisor", () => {
    localStorage.setItem("auth_user", JSON.stringify({ role: "member" }));
    const { container } = wrap(<HubCollabActions hub={baseHub} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows 记录回访 when feedback pending", () => {
    wrap(<HubCollabActions hub={{ ...baseHub, feedback_status: "pending" }} />);
    expect(screen.getByText("记录回访")).toBeInTheDocument();
  });

  it("shows 发版通知 when dev + done + not notified + not self_found", () => {
    wrap(
      <HubCollabActions
        hub={{ ...baseHub, status: "done", linear_status: "done", release_notified_at: null }}
      />,
    );
    expect(screen.getByText("发版通知")).toBeInTheDocument();
  });

  it("hides 发版通知 for self_found hubs even when done", () => {
    wrap(
      <HubCollabActions
        hub={{ ...baseHub, status: "done", linear_status: "done", self_found: true }}
      />,
    );
    expect(screen.queryByText("发版通知")).not.toBeInTheDocument();
  });

  it("shows 24h 内已催 (disabled) when urged within 24h", () => {
    wrap(<HubCollabActions hub={{ ...baseHub, last_urged_at: new Date().toISOString() }} />);
    expect(screen.getByText("24h 内已催")).toBeInTheDocument();
  });
});
