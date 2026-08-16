import { useEffect, useRef, useState } from "react";
import { listVaultDir } from "../vault/api";

type Props = {
  // Returns an error message to show, or null on success (picker closes itself).
  onConfirm: (path: string) => Promise<string | null>;
  onCancel: () => void;
};

// Manual "save to…" placement for a new draft (ADR 0015): the user types a
// Vault-relative path. Existing dirs are offered as hints (suggested placement
// via classification is a later upgrade — A5/A6). Backend enforces .md-only and
// the traversal guard, so this stays a thin convenience.
export default function SavePicker({ onConfirm, onCancel }: Props) {
  const [path, setPath] = useState("untitled.md");
  const [dirs, setDirs] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
    void listVaultDir("").then((l) => setDirs(l.dirs)).catch(() => {});
  }, []);

  const submit = async () => {
    const target = path.trim();
    if (!target || busy) return;
    setBusy(true);
    setError(null);
    const msg = await onConfirm(target);
    setBusy(false);
    if (msg) setError(msg);
  };

  return (
    <div className="picker-backdrop" onClick={onCancel}>
      <div className="picker" onClick={(e) => e.stopPropagation()}>
        <div className="picker-title">Save to Vault</div>
        <input
          ref={inputRef}
          className="picker-input"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit();
            if (e.key === "Escape") onCancel();
          }}
          placeholder="folder/name.md"
          spellCheck={false}
        />
        {dirs.length > 0 && (
          <div className="picker-dirs">
            {dirs.map((d) => (
              <button key={d} className="picker-dir" onClick={() => setPath(`${d}/`)}>
                {d}/
              </button>
            ))}
          </div>
        )}
        {error && <div className="picker-error">{error}</div>}
        <div className="picker-actions">
          <button className="picker-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button className="picker-save" onClick={() => void submit()} disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
