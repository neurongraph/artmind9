/** Plain DOM, so every view renders the same inside Obsidian and in jsdom. */
export function el<K extends keyof HTMLElementTagNameMap>(
  parent: HTMLElement | DocumentFragment | null,
  tag: K,
  options: { cls?: string; text?: string; attr?: Record<string, string> } = {},
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (options.cls) node.className = options.cls;
  if (options.text !== undefined) node.textContent = options.text;
  for (const [name, value] of Object.entries(options.attr ?? {})) node.setAttribute(name, value);
  parent?.appendChild(node);
  return node;
}

/** A button that shows why it is disabled, when it is. */
export function actionButton(
  parent: HTMLElement,
  label: string,
  blockedReason: string | null,
  onClick: () => void,
): HTMLButtonElement {
  const button = el(parent, "button", { cls: "artmind-action", text: label });
  if (blockedReason) {
    button.disabled = true;
    button.title = blockedReason;
    el(parent, "span", { cls: "artmind-blocked", text: blockedReason });
  } else {
    button.addEventListener("click", () => onClick());
  }
  return button;
}
