import { useState } from "react";
import { matchPages } from "./pageSearch";
import { Icon } from "./ui";

export interface SearchPage {
  id: string;
  label: string;
  icon: string;
}

/** The top-bar search shared by the apps: jump to a page by name, or hand the typed text to the
 *  app's own search ("Find patient ..." / "Find care ..."). It calls no server itself and keeps the
 *  text only while it is being typed. */
export function QuickSearch({ pages, onPage, fallback }: {
  pages: SearchPage[];                       // only pages that are built
  onPage: (id: string) => void;
  fallback: { icon: string; label: (text: string) => string; onChoose: (text: string) => void };
}) {
  const [text, setText] = useState("");
  const found = matchPages(pages, text);
  const word = text.trim();

  function choose(run: () => void) {
    run();
    setText("");
  }

  return (
    <div style={{ position: "relative" }}>
      <label className="search">
        <Icon name="search" size={18} />
        <input placeholder="Search pages or records" aria-label="Search pages or records" value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setText("");
            if (e.key === "Enter" && word) choose(found.length > 0 ? () => onPage(found[0].id) : () => fallback.onChoose(word));
          }} />
      </label>
      {word && (
        <div role="listbox" aria-label="Search results" className="card"
          style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 20, padding: 6, marginTop: 4 }}>
          {found.map((n) => (
            <button key={n.id} role="option" aria-selected={false} className="nav-item" onClick={() => choose(() => onPage(n.id))}>
              <Icon name={n.icon} />Go to {n.label}
            </button>
          ))}
          <button role="option" aria-selected={false} className="nav-item" onClick={() => choose(() => fallback.onChoose(word))}>
            <Icon name={fallback.icon} />{fallback.label(word)}
          </button>
        </div>
      )}
    </div>
  );
}
