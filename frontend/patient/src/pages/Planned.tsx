import { Card, Pill } from "../ui";

export function Planned({ title, waitsFor }: { title: string; waitsFor: string }) {
  return (
    <>
      <div className="hero"><div>
        <h1>{title}</h1>
        <p>This screen is part of the design but nothing real is behind it yet, so it shows no made-up data.</p>
      </div></div>
      <div className="grid">
        <Card title={<>{title} <Pill kind="warn">not built yet</Pill></>}>
          <div className="empty">{waitsFor}</div>
        </Card>
      </div>
    </>
  );
}
