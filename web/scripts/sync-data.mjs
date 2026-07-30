// Copies ../state/deals_history.json into public/data.json so the static app has
// something to fetch. Run before dev/build — never hand-edit public/data.json.
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "state", "deals_history.json");
const publicDir = join(here, "..", "public");
const dest = join(publicDir, "data.json");

const data = existsSync(src)
  ? readFileSync(src, "utf-8")
  : JSON.stringify({ entries: [] }, null, 2);

// public/ holds only this gitignored file, so git never tracks the directory itself —
// a fresh clone (e.g. Cloudflare's build environment) won't have it yet.
mkdirSync(publicDir, { recursive: true });
writeFileSync(dest, data);
console.log(`synced ${src} -> ${dest}`);
