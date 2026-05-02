/**
 * Harvester: claim token incentives ("buybacks") on every "agent" coin we
 * hold a position in.
 *
 * Why this exists: when pump.fun admin enables token incentives on a coin
 * they're "agent-curating", anyone who has volume on that coin can claim
 * a pro-rata share of the daily distributed supply. Our deploy snipe with
 * track_volume=true means we accrue claim eligibility from the moment we
 * fire — but we have to call `claim_token_incentives` to actually harvest.
 *
 * Run as a cron (e.g. every 6 hours) over a list of mints we hold.
 *
 *   npx tsx src/deployer/harvest_incentives.ts
 *
 * Resolves which coins to harvest from a JSON file at:
 *   ./data/agent_holdings.json
 * format:  [{"mint":"...","first_buy_sig":"...","strategy":"agent"}, ...]
 */
import "dotenv/config";
import {
  Connection,
  Keypair,
  TransactionMessage,
  VersionedTransaction,
  PublicKey,
  ComputeBudgetProgram,
} from "@solana/web3.js";
import fs from "node:fs";

import {
  buildClaimTokenIncentives,
  globalIncentiveTokenAccount,
} from "./pumpfun.js";

interface Holding {
  mint: string;
  strategy: "agent" | "meme";
  first_buy_sig?: string;
}

function loadKeypair(path: string): Keypair {
  const raw = JSON.parse(fs.readFileSync(path, "utf8"));
  return Keypair.fromSecretKey(Uint8Array.from(raw));
}

async function isIncentiveAccountFunded(
  conn: Connection,
  mint: PublicKey
): Promise<boolean> {
  const acct = globalIncentiveTokenAccount(mint);
  const info = await conn.getAccountInfo(acct);
  return !!info && info.lamports > 0;
}

async function main() {
  const conn = new Connection(process.env.SOLANA_RPC_URL!, "confirmed");
  const payer = loadKeypair(process.env.DEPLOYER_KEYPAIR_PATH!);

  const path = "./data/agent_holdings.json";
  if (!fs.existsSync(path)) {
    console.log("no agent holdings — nothing to claim");
    return;
  }
  const holdings: Holding[] = JSON.parse(fs.readFileSync(path, "utf8"));
  const agents = holdings.filter((h) => h.strategy === "agent");
  console.log(`checking ${agents.length} agent holdings for incentives`);

  for (const h of agents) {
    try {
      const mint = new PublicKey(h.mint);
      if (!(await isIncentiveAccountFunded(conn, mint))) {
        continue;
      }
      const ix = buildClaimTokenIncentives({
        user: payer.publicKey,
        mint,
        payer: payer.publicKey,
      });
      const { blockhash } = await conn.getLatestBlockhash("finalized");
      const msg = new TransactionMessage({
        payerKey: payer.publicKey,
        recentBlockhash: blockhash,
        instructions: [
          ComputeBudgetProgram.setComputeUnitLimit({ units: 80_000 }),
          ComputeBudgetProgram.setComputeUnitPrice({ microLamports: 1_000_000 }),
          ix,
        ],
      }).compileToV0Message();
      const tx = new VersionedTransaction(msg);
      tx.sign([payer]);
      const sig = await conn.sendTransaction(tx);
      console.log(`[harvest] ${h.mint} sig=${sig}`);
    } catch (e) {
      console.warn(`[harvest] ${h.mint} failed: ${(e as Error).message}`);
    }
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
