#!/usr/bin/env node
// Encrypt build/plain.json into data/vault.json using the same
// PBKDF2 + AES-GCM scheme app.js uses to decrypt in the browser.
//
// Never hardcode the ID/password here or anywhere else in this repo --
// pass them as environment variables so they never end up in git history.
//
//   SITE_ID=yourid SITE_PASSWORD=yourpass node tools/encrypt_vault.mjs
//
// Change the password any time by re-running this with a new SITE_PASSWORD
// and committing the resulting data/vault.json.

import { webcrypto } from "node:crypto";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PLAIN_PATH = path.join(ROOT, "build", "plain.json");
const VAULT_PATH = path.join(ROOT, "data", "vault.json");
const ITERATIONS = 600000;

function toB64(buf) {
  return Buffer.from(buf).toString("base64");
}

function fromB64(text) {
  return new Uint8Array(Buffer.from(text, "base64"));
}

async function chooseSalt() {
  if (process.env.ROTATE_KDF_SALT === "1") {
    return { salt: webcrypto.getRandomValues(new Uint8Array(16)), reused: false };
  }
  try {
    const existing = JSON.parse(await readFile(VAULT_PATH, "utf8"));
    if (existing.v === 1 && existing.kdf === "PBKDF2-SHA256" && existing.salt) {
      return { salt: fromB64(existing.salt), reused: true };
    }
  } catch (_) {
    // First build or an unreadable old vault: create a new salt.
  }
  return { salt: webcrypto.getRandomValues(new Uint8Array(16)), reused: false };
}

async function deriveKey(id, password, salt, usage) {
  const enc = new TextEncoder();
  const keyMaterial = await webcrypto.subtle.importKey(
    "raw", enc.encode(`${id}${password}`), "PBKDF2", false, ["deriveKey"]
  );
  return webcrypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations: ITERATIONS, hash: "SHA-256" },
    keyMaterial, { name: "AES-GCM", length: 256 }, false, [usage]
  );
}

async function main() {
  const id = process.env.SITE_ID;
  const password = process.env.SITE_PASSWORD;
  if (!id || !password) {
    console.error("Set SITE_ID and SITE_PASSWORD environment variables first.");
    process.exit(1);
  }

  const plain = await readFile(PLAIN_PATH, "utf8");
  const enc = new TextEncoder();
  const saltChoice = await chooseSalt();
  const salt = saltChoice.salt;
  const iv = webcrypto.getRandomValues(new Uint8Array(12));

  const encryptKey = await deriveKey(id, password, salt, "encrypt");
  const ciphertext = await webcrypto.subtle.encrypt({ name: "AES-GCM", iv }, encryptKey, enc.encode(plain));

  const vault = {
    v: 1,
    kdf: "PBKDF2-SHA256",
    iterations: ITERATIONS,
    salt: toB64(salt),
    iv: toB64(iv),
    ct: toB64(ciphertext),
  };

  // Never write a vault we can't prove decrypts back to the same plaintext.
  const decryptKey = await deriveKey(id, password, salt, "decrypt");
  const checkPt = await webcrypto.subtle.decrypt({ name: "AES-GCM", iv }, decryptKey, ciphertext);
  const roundTripOk = new TextDecoder().decode(checkPt) === plain;
  if (!roundTripOk) {
    console.error("Self-check failed: encrypted vault did not decrypt back to the source plaintext. Not writing data/vault.json.");
    process.exit(1);
  }

  await mkdir(path.dirname(VAULT_PATH), { recursive: true });
  await writeFile(VAULT_PATH, JSON.stringify(vault), "utf8");
  console.log(`wrote ${VAULT_PATH} (${JSON.stringify(vault).length.toLocaleString()} bytes), self-check passed`);
  console.log(saltChoice.reused
    ? "reused existing KDF salt (cached login key remains valid when credentials are unchanged)"
    : "created new KDF salt");
}

main();
