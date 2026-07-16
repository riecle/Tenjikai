#!/usr/bin/env node
// Decrypt data/vault.json into build/plain.json.
//
// Credentials are accepted only through environment variables and are never
// written to the repository:
//
//   SITE_ID=... SITE_PASSWORD=... node tools/decrypt_vault.mjs
//
// build/plain.json is git-ignored. Delete it after the update if the machine is
// shared with other people.

import { webcrypto } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const VAULT_PATH = path.join(ROOT, "data", "vault.json");
const PLAIN_PATH = path.join(ROOT, "build", "plain.json");

function fromB64(text) {
  return new Uint8Array(Buffer.from(text, "base64"));
}

async function deriveKey(id, password, vault) {
  const enc = new TextEncoder();
  const keyMaterial = await webcrypto.subtle.importKey(
    "raw", enc.encode(`${id}${password}`), "PBKDF2", false, ["deriveKey"]
  );
  return webcrypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt: fromB64(vault.salt),
      iterations: Number(vault.iterations),
      hash: "SHA-256",
    },
    keyMaterial,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"]
  );
}

async function main() {
  const id = process.env.SITE_ID;
  const password = process.env.SITE_PASSWORD;
  if (!id || !password) {
    console.error("Set SITE_ID and SITE_PASSWORD environment variables first.");
    process.exit(1);
  }

  const vault = JSON.parse(await readFile(VAULT_PATH, "utf8"));
  if (vault.v !== 1 || vault.kdf !== "PBKDF2-SHA256") {
    throw new Error(`Unsupported vault format: v=${vault.v}, kdf=${vault.kdf}`);
  }

  try {
    const key = await deriveKey(id, password, vault);
    const plaintext = await webcrypto.subtle.decrypt(
      { name: "AES-GCM", iv: fromB64(vault.iv) },
      key,
      fromB64(vault.ct)
    );
    const text = new TextDecoder().decode(plaintext);
    const payload = JSON.parse(text);
    if (!payload || !Array.isArray(payload.rows) || typeof payload.meta !== "object") {
      throw new Error("Decrypted JSON does not match the expected Tenjikai payload.");
    }

    await mkdir(path.dirname(PLAIN_PATH), { recursive: true });
    await writeFile(PLAIN_PATH, text, { encoding: "utf8", mode: 0o600 });
    console.log(`wrote ${PLAIN_PATH} (${payload.rows.length.toLocaleString()} rows)`);
    console.log("The plaintext file is git-ignored. Delete build/plain.json after re-encryption on a shared machine.");
  } catch (error) {
    console.error("Decryption failed. Check the exact ID/password capitalization and spacing.");
    if (process.env.DEBUG_VAULT === "1") console.error(error);
    process.exit(2);
  }
}

main();
