import { useEffect, useRef } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { basicSetup } from "codemirror";

type Props = {
  // Bumps whenever the *whole document* must be replaced (tab switch, reload,
  // conflict resolve). While it's unchanged, the user types freely and we never
  // reset from props — so the caret and undo history survive re-renders.
  docKey: string;
  initialValue: string;
  onChange: (text: string) => void;
  onSave: () => void;
};

// A thin CodeMirror 6 host. The view is created once; `docKey` drives full-doc
// replacement. User edits flow out through `onChange`; Cmd/Ctrl-S calls `onSave`.
export default function CodeMirrorEditor({ docKey, initialValue, onChange, onSave }: Props) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const docKeyRef = useRef<string>(docKey);
  // Suppress the change callback while WE replace the doc programmatically.
  const syncingRef = useRef(false);
  // Latest closures, read by long-lived editor extensions without rebuilding them.
  const onChangeRef = useRef(onChange);
  const onSaveRef = useRef(onSave);
  onChangeRef.current = onChange;
  onSaveRef.current = onSave;

  // Create the editor once.
  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: initialValue,
        extensions: [
          basicSetup,
          history(),
          keymap.of([
            {
              key: "Mod-s",
              preventDefault: true,
              run: () => {
                onSaveRef.current();
                return true;
              },
            },
            ...historyKeymap,
            ...defaultKeymap,
          ]),
          markdown(),
          EditorView.lineWrapping,
          EditorView.theme(
            {
              "&": { height: "100%", fontSize: "13px", background: "transparent" },
              ".cm-scroller": {
                fontFamily:
                  "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
                lineHeight: "1.5",
              },
              ".cm-content": { caretColor: "#e6e8ec" },
              "&.cm-focused .cm-cursor": { borderLeftColor: "#e6e8ec" },
              ".cm-gutters": {
                background: "transparent",
                color: "#5b6472",
                border: "none",
              },
              "&.cm-focused": { outline: "none" },
              ".cm-activeLine, .cm-activeLineGutter": { background: "transparent" },
            },
            { dark: true },
          ),
          EditorView.updateListener.of((u) => {
            if (u.docChanged && !syncingRef.current) {
              onChangeRef.current(u.state.doc.toString());
            }
          }),
        ],
      }),
    });
    viewRef.current = view;
    docKeyRef.current = docKey;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // Created once; initialValue/docKey for this first mount are captured above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Replace the whole document when docKey changes (tab switch / reload).
  useEffect(() => {
    const view = viewRef.current;
    if (!view || docKeyRef.current === docKey) return;
    docKeyRef.current = docKey;
    syncingRef.current = true;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: initialValue },
    });
    syncingRef.current = false;
  }, [docKey, initialValue]);

  return <div className="cm-host" ref={hostRef} />;
}
