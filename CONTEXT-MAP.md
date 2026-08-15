# Context Map

## Contexts

- [artmind_canvas](./artmind_canvas/CONTEXT.md) — a new standalone desktop UX (spatial
  Canvas + Chat dock) for reading, understanding, editing, and acting on artmind's
  knowledge. A client of the existing artmind system.

> The core `artmind` knowledge-graph system does not yet have its own `CONTEXT.md`;
> add one here if/when we model its vocabulary explicitly.

## Relationships

- **artmind_canvas → artmind**: artmind_canvas is a client of the artmind brain. It
  reuses artmind's Python modules and the neutral agent-backend contract, and drives
  the `artmind` CLI grammar; it never reimplements the graph or ingestion layers.
