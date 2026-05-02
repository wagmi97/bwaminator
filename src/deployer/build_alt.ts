/**
 * One-time setup helper. Pre-publishes an Address Lookup Table containing
 * every static account the deploy bundle references (program IDs, sysvars,
 * pumpfun PDAs, the deployer wallet). Run this once, paste the resulting
 * pubkey into ALT_PUBKEYS in .env, and every subsequent deploy tx is ~30%
 * smaller on the wire.
 */
import "dotenv/config";
import {
  Connection,
  Keypair,
  PublicKey,
  AddressLookupTableProgram,
  TransactionMessage,
  VersionedTransaction,
  SystemProgram,
  SYSVAR_RENT_PUBKEY,
} from "@solana/web3.js";
import {
  TOKEN_2022_PROGRAM_ID,
  ASSOCIATED_TOKEN_PROGRAM_ID,
} from "@solana/spl-token";
import fs from "node:fs";

import {
  PUMP_PROGRAM,
  PUMP_FEE_PROGRAM,
  DONT_BUNDLE_FLAG,
  DONT_FRONT_FLAG,
} from "./pumpfun.js";

function loadKeypair(path: string): Keypair {
  const raw = JSON.parse(fs.readFileSync(path, "utf8"));
  return Keypair.fromSecretKey(Uint8Array.from(raw));
}

async function main() {
  const conn = new Connection(process.env.SOLANA_RPC_URL!, "confirmed");
  const payer = loadKeypair(process.env.DEPLOYER_KEYPAIR_PATH!);

  const slot = await conn.getSlot("finalized");
  const [createIx, altPubkey] = AddressLookupTableProgram.createLookupTable({
    authority: payer.publicKey,
    payer: payer.publicKey,
    recentSlot: slot,
  });

  const STATIC_ADDRS = [
    SystemProgram.programId,
    SYSVAR_RENT_PUBKEY,
    PUMP_PROGRAM,
    PUMP_FEE_PROGRAM,
    TOKEN_2022_PROGRAM_ID,
    ASSOCIATED_TOKEN_PROGRAM_ID,
    DONT_BUNDLE_FLAG,
    DONT_FRONT_FLAG,
    payer.publicKey,
    new PublicKey("TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"), // mint authority PDA
  ];

  const extendIx = AddressLookupTableProgram.extendLookupTable({
    payer: payer.publicKey,
    authority: payer.publicKey,
    lookupTable: altPubkey,
    addresses: STATIC_ADDRS,
  });

  const { blockhash } = await conn.getLatestBlockhash("finalized");
  const msg = new TransactionMessage({
    payerKey: payer.publicKey,
    recentBlockhash: blockhash,
    instructions: [createIx, extendIx],
  }).compileToV0Message();
  const tx = new VersionedTransaction(msg);
  tx.sign([payer]);

  const sig = await conn.sendTransaction(tx);
  console.log(JSON.stringify({ alt: altPubkey.toBase58(), signature: sig }, null, 2));
  console.log("\nPaste this into ALT_PUBKEYS in .env:");
  console.log(altPubkey.toBase58());
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
