import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Tests run in plain Node. Obsidian's API only exists inside the app, so
// `obsidian` resolves to a small stand-in (test/mocks/obsidian.ts).
export default defineConfig({
  resolve: {
    alias: { obsidian: fileURLToPath(new URL("./test/mocks/obsidian.ts", import.meta.url)) },
  },
  test: {
    include: ["test/**/*.test.ts"],
    environment: "node",
  },
});
