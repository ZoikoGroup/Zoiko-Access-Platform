import { useState } from "react";
import { getCompliance } from "../api";
import type { Evidence } from "../types";
import { Gate, useLoad } from "../useLoad";
import { Card, Pill, Table, timeOf } from "../ui";

export function Compliance() {
  const result = useLoad(getCompliance);
  return <Gate result={result}>{(data) => <View data={data} />}</Gate>;
}

function View({ data }: { data: Evidence }) {
  const [onlyDenied, setOnlyDenied] = useState(false);
  const decisions = data.decisions.filter((d) => !onlyDenied || !d.allowed);
  const chain = data.chain;
  return (
    <>
      <div className="hero">
        <div>
          <h1>Compliance</h1>
          <p>Every access check and every change is recorded. The audit log cannot be edited, and tampering would break its chain.</p>
        </div>
        <span className="spacer" />
        {chain && (chain.ok
          ? <Pill kind="ok">Audit chain intact ({chain.checked} entries)</Pill>
          : <Pill kind="bad">TAMPERING at #{chain.bad_seq}: {chain.problem}</Pill>)}
      </div>

      <div className="grid">
        <Card title="Access decisions" sub="Every check by the policy engine, newest first"
          action={
            <label style={{ fontSize: 13 }}>
              <input type="checkbox" checked={onlyDenied} onChange={(e) => setOnlyDenied(e.target.checked)} /> denied only
            </label>
          }>
          <Table headers={["Time (UTC)", "Who", "Operation", "Purpose", "Result", "Reason"]}
            empty="No matching decisions."
            rows={decisions.map((d) => [
              timeOf(d.at), d.actor, d.operation, d.purpose,
              <Pill key="r" kind={d.allowed ? "ok" : "bad"}>{d.allowed ? "ALLOWED" : "DENIED"}</Pill>, d.reason,
            ])} />
        </Card>

        <Card title="Audit log" sub="Each entry carries a fingerprint of the one before it">
          <Table headers={["#", "Time (UTC)", "Who", "Action", "Result", "Object", "Fingerprint", "Previous"]}
            empty="Nothing audited yet."
            rows={data.events.map((e) => [
              e.seq, timeOf(e.at), e.actor, e.action, e.outcome + (e.reason ? ` / ${e.reason}` : ""),
              e.object.length > 28 ? `${e.object.slice(0, 28)}…` : e.object,
              <span key="h" className="mono">{e.hash}</span>, <span key="p" className="mono">{e.prev}</span>,
            ])} />
        </Card>
      </div>
    </>
  );
}
