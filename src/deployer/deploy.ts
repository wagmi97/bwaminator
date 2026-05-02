/**
 * Build + send the deploy bundle.
 *
 * Flow per shot:
 *   1. Generate (or load from a vanity-grind cache) a fresh mint keypair
 *      whose pubkey ends in "pump".
 *   2. Hydrate a pre-signed durable-nonce template with the new
 *      mint + name + symbol + uri.
 *   3. Sign with deployer + mint.
 *   4. Submit to NextBlock (or Jito), don't wait for finality — we land
 *      via the relayer, then poll signature status async.
 *
 * Latency target on a warm pool: 200–500ms from call() to wire send.
 */
import "dotenv/config";
import {
  Connection,
  Keypair,
  PublicKey,
  TransactionMessage,
  VersionedTransaction,
  AddressLookupTableAccount,
  ComputeBudgetProgram,
  SystemProgram,
  NONCE_ACCOUNT_LENGTH,
  NonceAccount,
} from "@solana/web3.js";
import {
  getAssociatedTokenAddressSync,
  TOKEN_2022_PROGRAM_ID,
} from "@solana/spl-token";
import BN from "bn.js";
import bs58 from "bs58";
import fs from "node:fs";

import {
  PUMP_PROGRAM,
  buildCreateV2,
  buildBuyExactSolIn,
  antiMevFlagAccounts,
  type DeployStrategy,
} from "./pumpfun.js";
import { popMintKeypair } from "./mint_pool.js";
import { buildRelayers, buildTipInstructions, raceSend } from "./multi_relayer.js";

const SOL = 1_000_000_000;

interface DeployRequest {
  name: string;
  symbol: string;
  metadataUri: string;
  snipeSol: number;
  /** "meme" enables cashback (default), "agent" disables cashback so creator
   *  keeps fees. The namer classifies the topic in `namer.py`. */
  strategy: DeployStrategy;
}

function loadKeypair(path: string): Keypair {
  const raw = JSON.parse(fs.readFileSync(path, "utf8"));
  return Keypair.fromSecretKey(Uint8Array.from(raw));
}

/** Pop a pre-grinded "...pump"-suffix keypair from the disk-backed pool the
 *  worker (mint_pool.ts) keeps topped up. Falls back to synchronous grind
 *  if the pool is empty. */
function nextMint(): Keypair {
  return popMintKeypair();
}

async function loadAlts(conn: Connection, alts: string[]): Promise<AddressLookupTableAccount[]> {
  const out: AddressLookupTableAccount[] = [];
  for (const k of alts) {
    const a = await conn.getAddressLookupTable(new PublicKey(k));
    if (a.value) out.push(a.value);
  }
  return out;
}

async function buildDeployTx(
  conn: Connection,
  req: DeployRequest,
  deployer: Keypair,
  mint: Keypair,
  nonceAccountPk: PublicKey,
  nonceAuthority: Keypair,
  altPks: string[],
  tipInstructions: ReturnType<typeof buildTipInstructions>,
  priorityFeeMicroLamportsPerCu: number
): Promise<VersionedTransaction> {
  // 1. Fetch the nonce so we know the current "blockhash" to use.
  const nonceInfo = await conn.getAccountInfo(nonceAccountPk);
  if (!nonceInfo) throw new Error("nonce account not found");
  const nonce = NonceAccount.fromAccountData(nonceInfo.data);

  // 2. Compute the deployer's bonding-curve token account address. The
  //    on-chain trace uses createAccountWithSeed (deterministic), but the
  //    standard ATA derivation works just as well and is simpler.
  const payerAta = getAssociatedTokenAddressSync(
    mint.publicKey,
    deployer.publicKey,
    false,
    TOKEN_2022_PROGRAM_ID
  );

  const ixs = [
    // (a) Advance the nonce. MUST be the first instruction.
    SystemProgram.nonceAdvance({
      noncePubkey: nonceAccountPk,
      authorizedPubkey: nonceAuthority.publicKey,
    }),
    // (b) Anti-MEV hint: ComputeBudget no-op carrying flag accounts
    new (await import("@solana/web3.js")).TransactionInstruction({
      programId: ComputeBudgetProgram.programId,
      keys: antiMevFlagAccounts(),
      data: Buffer.from(
        ComputeBudgetProgram.setComputeUnitLimit({ units: 350_000 }).data
      ),
    }),
    // (c) Real CU price
    ComputeBudgetProgram.setComputeUnitPrice({
      microLamports: priorityFeeMicroLamportsPerCu,
    }),
    // (d) Pumpfun CreateV2 — meme=cashback on, agent=cashback off
    buildCreateV2({
      payer: deployer.publicKey,
      mint: mint.publicKey,
      creator: deployer.publicKey,
      name: req.name,
      symbol: req.symbol,
      uri: req.metadataUri,
      strategy: req.strategy,
    }),
    // (e) Pumpfun BuyExactSolIn — the snipe. min_tokens=1 == "any fill".
    //     trackVolume=true on memes so we accrue cashback on our own buy.
    buildBuyExactSolIn({
      payer: deployer.publicKey,
      mint: mint.publicKey,
      creator: deployer.publicKey,
      spendableSolIn: new BN(Math.floor(req.snipeSol * SOL)),
      minTokensOut: new BN(1),
      trackVolume: req.strategy === "meme" ? true : false,
      isCashbackCoin: req.strategy === "meme",
    }),
    // (f) One tip per relayer in the race — whichever wins, we paid them.
    ...tipInstructions,
  ];

  const alts = await loadAlts(conn, altPks);
  const message = new TransactionMessage({
    payerKey: deployer.publicKey,
    recentBlockhash: nonce.nonce,
    instructions: ixs,
  }).compileToV0Message(alts);

  const tx = new VersionedTransaction(message);
  tx.sign([deployer, mint, nonceAuthority]);
  return tx;
}

export async function deploy(
  req: DeployRequest
): Promise<{ mint: string; signature: string; via: string; raced: { name: string; ms: number }[] }> {
  const conn = new Connection(process.env.SOLANA_RPC_URL!, "confirmed");
  const deployer = loadKeypair(process.env.DEPLOYER_KEYPAIR_PATH!);
  const nonceAuthority = loadKeypair(process.env.NONCE_AUTHORITY_KEYPAIR_PATH!);

  const noncePool = (process.env.NONCE_ACCOUNT_PUBKEYS ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (noncePool.length === 0) throw new Error("NONCE_ACCOUNT_PUBKEYS empty");
  const nonceAccount = new PublicKey(noncePool[Math.floor(Math.random() * noncePool.length)]);

  const alts = (process.env.ALT_PUBKEYS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const relayers = buildRelayers();
  if (relayers.length === 0) throw new Error("no relayers configured");

  const tipLamports = Number(process.env.LANDING_TIP_LAMPORTS ?? "1000000");
  const tipIxs = buildTipInstructions(deployer.publicKey, relayers, tipLamports);

  const mint = nextMint();
  const tx = await buildDeployTx(
    conn,
    req,
    deployer,
    mint,
    nonceAccount,
    nonceAuthority,
    alts,
    tipIxs,
    Number(process.env.PRIORITY_FEE_LAMPORTS_PER_CU ?? "2860000")
  );

  const rawTx = tx.serialize();

  if (process.env.DRY_RUN === "true") {
    return {
      mint: mint.publicKey.toBase58(),
      signature: bs58.encode(tx.signatures[0]),
      via: "dry-run",
      raced: [],
    };
  }

  const result = await raceSend(rawTx, relayers);
  return { mint: mint.publicKey.toBase58(), ...result };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  // Orchestrator passes a JSON request via $DEPLOY_REQ; default to a CLI test.
  const reqRaw = process.env.DEPLOY_REQ;
  const req: DeployRequest = reqRaw
    ? JSON.parse(reqRaw)
    : {
        name: "test coin",
        symbol: "TEST",
        metadataUri: "https://example.com/metadata.json",
        snipeSol: 0.1,
        strategy: "meme",
      };
  deploy(req)
    .then((r) => console.log(JSON.stringify(r, null, 2)))
    .catch((e) => {
      console.error(e);
      process.exit(1);
    });
}
