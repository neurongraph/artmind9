import { createContext, useContext } from "react";

// Card-frame actions injected by App via context rather than through `node.data`,
// so `nodeToCard` (boards/mapping) never tries to serialize a function into the
// board store. Every Card's shared shell (CardShell) reads `onClose` from here.
export type CardActions = {
  onClose: (id: string) => void;
};

const CardActionsContext = createContext<CardActions>({
  onClose: () => {},
});

export const CardActionsProvider = CardActionsContext.Provider;

export function useCardActions(): CardActions {
  return useContext(CardActionsContext);
}
