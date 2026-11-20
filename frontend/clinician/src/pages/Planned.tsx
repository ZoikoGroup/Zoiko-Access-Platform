import type { NavItem } from "../nav";
import { Card, Pill } from "../ui";

export function Planned({ item }: { item: NavItem }) {
  return (
    <>
      <div className="hero"><div>
        <h1>{item.label}</h1>
        <p>This screen is part of the design but has nothing real behind it yet, so it shows no made-up data.</p>
      </div></div>
      <div className="grid">
        <Card title={<>{item.label} <Pill kind="warn">not built yet</Pill></>}>
          <div className="empty">{item.waitsFor}</div>
        </Card>
      </div>
    </>
  );
}
