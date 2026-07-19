// Seed Foundry world settings (LevelDB) while the world is offline.
//
// Usage:
//   node seed_settings.mjs <foundry_app_dir> <world_settings_db_path> <json>
//
// <json> is an object mapping setting keys to raw (already-parsed) values,
// e.g. {"core.moduleConfiguration": {"oronder": true}, "oronder.auth": "tok"}.
// Values are JSON-stringified into the setting document's value field, the
// way Foundry stores them. Existing entries are updated in place; missing
// ones are created. Requires the Foundry server to be stopped (LevelDB lock).

import { createRequire } from "node:module";
import path from "node:path";

const [appDir, dbPath, json] = process.argv.slice(2);
if (!appDir || !dbPath || !json) {
  console.error("usage: seed_settings.mjs <foundry_app_dir> <settings_db> <json>");
  process.exit(2);
}

const require = createRequire(path.join(appDir, "/"));
const { ClassicLevel } = require("classic-level");

const wanted = JSON.parse(json);

function randomId(n = 16) {
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < n; i++)
    out += chars[Math.floor(Math.random() * chars.length)];
  return out;
}

const db = new ClassicLevel(dbPath, {
  keyEncoding: "utf8",
  valueEncoding: "utf8",
});
await db.open();

const existing = new Map(); // setting key -> [dbKey, doc]
for await (const [k, v] of db.iterator()) {
  if (!k.startsWith("!settings!")) continue;
  try {
    const doc = JSON.parse(v);
    if (doc.key) existing.set(doc.key, [k, doc]);
  } catch {
    /* ignore non-JSON rows */
  }
}

const batch = db.batch();
for (const [key, value] of Object.entries(wanted)) {
  const stringified = JSON.stringify(value);
  if (existing.has(key)) {
    const [dbKey, doc] = existing.get(key);
    doc.value = stringified;
    batch.put(dbKey, JSON.stringify(doc));
    console.error(`updated ${key}`);
  } else {
    const id = randomId();
    const doc = {
      key,
      value: stringified,
      _id: id,
      user: null,
      _stats: {
        coreVersion: "14.364",
        systemId: null,
        systemVersion: null,
        createdTime: Date.now(),
        modifiedTime: Date.now(),
        lastModifiedBy: null,
      },
    };
    batch.put(`!settings!${id}`, JSON.stringify(doc));
    console.error(`created ${key}`);
  }
}
await batch.write();
await db.close();
console.log("ok");
