# Web-first, defer the native shell (Tauri)

artmind_canvas is a single-user desktop UX; Rust/Tauri was floated but the goal
(Q3) is best UX, not learning a stack. We will build a pure web app (JS framework
TBD) and keep it shell-agnostic so it can be wrapped in Tauri later without a
rewrite.

Why: every required widget — infinite canvas, graph visualization, markdown block
editor, ad-hoc micro-UIs — is a mature web/JS library, and the artmind brain already
speaks HTTP/SSE, so a native client buys nothing structural. Tauri's real value is
packaging (native window, OS integration, single installable), which is a small
end-of-project step. Starting with Tauri would front-load two unfamiliar
technologies (Rust + Tauri) to buy something not yet needed.
