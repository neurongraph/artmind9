import { useCardActions } from "./cardActions";

// Shown in a card body when its live fetch fails. A "not found" almost always
// means the underlying Vault file or graph entity is gone — the card is *stale*
// (e.g. a saved board card whose document was deleted, or a reference that no
// longer resolves). Rather than stranding a raw "Error: file not found" string
// reachable only via the tiny header ×, we surface a clear message and a
// prominent Dismiss that removes the card (onClose, the same action the header
// × uses). Non-404 errors (backend down, bad request) keep the raw detail so
// they stay debuggable.
export default function CardError({ id, message }: { id: string; message: string }) {
  const { onClose } = useCardActions();
  const detail = message.replace(/^Error:\s*/i, "");
  const stale = /not found|404|no such|does not exist|deleted/i.test(detail);
  return (
    <div className="doc-card-error">
      <div className="doc-card-error-head">
        {stale ? "This reference no longer resolves" : "Couldn't load this card"}
      </div>
      <div className="doc-card-error-detail">{detail}</div>
      <button
        className="doc-card-error-dismiss nodrag"
        onClick={() => onClose(id)}
        title="Remove this card from the board"
      >
        Dismiss card
      </button>
    </div>
  );
}
