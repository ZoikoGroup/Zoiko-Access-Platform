import { Card } from "../ui";

export function Documents() {
  return (
    <>
      <div className="hero"><div>
        <h1>API documents</h1>
        <p>The descriptions of what Zoiko Access offers and sends. They are generated from the running system, so they list only what exists. They hold no examples and no patient data.</p>
      </div></div>
      <div className="grid">
        <Card title="The API (OpenAPI 3.1)">
          <p style={{ margin: "0 0 8px" }}>Every route, method, login requirement and the standard error shape.</p>
          <a className="btn" href="/api/v1/openapi.json" target="_blank" rel="noopener">Open openapi.json</a>
        </Card>
        <Card title="The events (AsyncAPI 3.0)">
          <p style={{ margin: "0 0 8px" }}>Every event Zoiko Access publishes, which thing it is about and its reference fields, with a schema hash that changes when the event changes.</p>
          <a className="btn" href="/api/v1/asyncapi.json" target="_blank" rel="noopener">Open asyncapi.json</a>
        </Card>
        <Card title="Good to know">
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            <li>Only version 1 exists. Any other version gets a not-found answer that lists the versions served.</li>
            <li>Webhook messages are signed with your secret over the time, the delivery id and the body.</li>
            <li>Repeating a request with the same Idempotency-Key and the same body gives the same answer.</li>
          </ul>
        </Card>
      </div>
    </>
  );
}
