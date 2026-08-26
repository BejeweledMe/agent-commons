import type { ReactNode } from "react";

type WorkflowCardProps = {
  title: string;
  ready: boolean;
  children: ReactNode;
};

export function WorkflowCard({ title, ready, children }: WorkflowCardProps): ReactNode {
  return (
    <section className="workflow-card" aria-label={title}>
      <header className="workflow-card-header">
        <h2>{title}</h2>
        <span aria-hidden="true" className={ready ? "status status-ready" : "status"}>{ready ? "✓" : "…"}</span>
      </header>
      {children}
    </section>
  );
}
