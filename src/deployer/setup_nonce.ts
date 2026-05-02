/**
 * One-time setup. Creates N durable nonce accounts owned by NONCE_AUTHORITY
 * and prints their pubkeys. Pre-creating a pool of nonces is what lets the
 * runtime sign+send instantly without a `getLatestBlockhash` round-trip.
 *
 * Usage:  npx tsx src/deployer/setup_nonce.ts 8
 */
import "dotenv/config";
import {
  Connection,
  Keypair,
  PublicKey,
  SystemProgram,
  TransactionMessage,
  VersionedTransaction,
  NONCE_ACCOUNT_LENGTH,
} from "@solana/web3.js";
import fs from "node:fs";

function loadKeypair(path: string): Keypair {
  const raw = JSON.parse(fs.readFileSync(path, "utf8"));
  return Keypair.fromSecretKey(Uint8Array.from(raw));
}

async function main() {
  const n = Number(process.argv[2] ?? "4");
  const conn = new Connection(process.env.SOLANA_RPC_URL!, "confirmed");
  const payer = loadKeypair(process.env.DEPLOYER_KEYPAIR_PATH!);
  const authority = loadKeypair(process.env.NONCE_AUTHORITY_KEYPAIR_PATH!);

  const rent = await conn.getMinimumBalanceForRentExemption(NONCE_ACCOUNT_LENGTH);
  const created: string[] = [];
  for (let i = 0; i < n; i++) {
    const nonceKp = Keypair.generate();
    const ixs = [
      SystemProgram.createAccount({
        fromPubkey: payer.publicKey,
        newAccountPubkey: nonceKp.publicKey,
        lamports: rent,
        space: NONCE_ACCOUNT_LENGTH,
        programId: SystemProgram.programId,
      }),
      SystemProgram.nonceInitialize({
        noncePubkey: nonceKp.publicKey,
        authorizedPubkey: authority.publicKey,
      }),
    ];
    const { blockhash } = await conn.getLatestBlockhash("finalized");
    const msg = new TransactionMessage({
      payerKey: payer.publicKey,
      recentBlockhash: blockhash,
      instructions: ixs,
    }).compileToV0Message();
    const tx = new VersionedTransaction(msg);
    tx.sign([payer, nonceKp]);
    const sig = await conn.sendTransaction(tx);
    await conn.confirmTransaction(sig, "confirmed");
    created.push(nonceKp.publicKey.toBase58());
    console.log(`[${i + 1}/${n}] nonce ${nonceKp.publicKey.toBase58()}  sig ${sig}`);
  }
  console.log("\nPaste this into NONCE_ACCOUNT_PUBKEYS in .env (comma-separated):");
  console.log(created.join(","));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
