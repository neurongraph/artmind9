#!/usr/bin/env node
// A stand-in `artmind` for ArtmindCli's tests. FAKE_ARTMIND_RULES names a
// JSON file of [{match, stdout, stderr, exit, delayMs}]: the first rule
// whose `match` starts the joined args answers. FAKE_ARTMIND_LOG, when set,
// gets a "start <args>" and an "end <args>" line per run.
import { appendFileSync, readFileSync } from "node:fs";

const key = process.argv.slice(2).join(" ");
const rules = JSON.parse(readFileSync(process.env.FAKE_ARTMIND_RULES, "utf8"));
const rule = rules.find((r) => key.startsWith(r.match)) ?? { stderr: `no rule for ${key}`, exit: 99 };
const log = process.env.FAKE_ARTMIND_LOG;
if (log) appendFileSync(log, `start ${key}\n`);
setTimeout(() => {
  if (log) appendFileSync(log, `end ${key}\n`);
  process.stdout.write(rule.stdout ?? "");
  process.stderr.write(rule.stderr ?? "");
  process.exit(rule.exit ?? 0);
}, rule.delayMs ?? 0);
