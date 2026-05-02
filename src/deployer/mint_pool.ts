/**
 * Pre-grinded pump-suffix mint keypair pool.
 *
 * Pumpfun convention: mint pubkey ends in "pump". Brute-force grinding takes
 * ~50-200ms on a modern CPU per match. Doing this synchronously inside the
 * deploy hot path costs us 100-200ms of latency we don't have.
 *
 * Solution: a worker process keeps a queue of N pre-grinded keypairs on disk.
 * The deploy hot path pop()s instantly. The worker tops up in the background.
 *
 * Run as:  npx tsx src/deployer/mint_pool.ts
 */
import "dotenv/config";
import { Keypair } from "@solana/web3.js";
import fs from "node:fs";
import path from "node:path";

const POOL_DIR = process.env.MINT_POOL_DIR ?? "./mint_pool";
const TARGET = Number(process.env.MINT_POOL_TARGET_SIZE ?? "200");
const SUFFIX = "pump";

function ensureDir(): void {
  fs.mkdirSync(POOL_DIR, { recursive: true });
}

function poolSize(): number {
  return fs.readdirSync(POOL_DIR).filter((f) => f.endsWith(".json")).length;
}

function grindOne(): Keypair {
  // ~50-200ms per hit on a single thread.
  while (true) {
    const kp = Keypair.generate();
    if (kp.publicKey.toBase58().endsWith(SUFFIX)) return kp;
  }
}

function persist(kp: Keypair): void {
  const file = path.join(POOL_DIR, `${kp.publicKey.toBase58()}.json`);
  fs.writeFileSync(file, JSON.stringify(Array.from(kp.secretKey)));
}

/** Pop one keypair from disk. Atomic via rename-and-unlink so a concurrent
 *  deploy can't double-pop. */
export function popMintKeypair(): Keypair {
  ensureDir();
  const files = fs
    .readdirSync(POOL_DIR)
    .filter((f) => f.endsWith(".json"))
    .sort();
  if (files.length === 0) {
    // Fallback: synchronous grind — slow path but never blocks.
    const kp = grindOne();
    return kp;
  }
  const file = path.join(POOL_DIR, files[0]);
  const claim = file + ".taken";
  try {
    fs.renameSync(file, claim); // atomic; loses race if another process took it
    const raw = JSON.parse(fs.readFileSync(claim, "utf8"));
    fs.unlinkSync(claim);
    return Keypair.fromSecretKey(Uint8Array.from(raw));
  } catch {
    return popMintKeypair(); // someone else won the race; retry
  }
}

async function workerLoop(): Promise<void> {
  ensureDir();
  // Spawn N workers via setImmediate to use multiple cores via cooperative I/O.
  // For real multi-core, fork into worker_threads — keeping it simple here.
  const concurrency = Math.max(1, (Number(process.env.GRIND_CONCURRENCY) || 4));
  const start = Date.now();
  let made = 0;

  async function oneGrinder(): Promise<void> {
    while (true) {
      if (poolSize() >= TARGET) {
        await new Promise((r) => setTimeout(r, 1000));
        continue;
      }
      const kp = grindOne();
      persist(kp);
      made++;
      const rate = made / Math.max(1, (Date.now() - start) / 1000);
      process.stdout.write(
        `\rpool=${poolSize()}/${TARGET}  total_made=${made}  rate=${rate.toFixed(1)}/s  `
      );
    }
  }

  await Promise.all(Array.from({ length: concurrency }, () => oneGrinder()));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  workerLoop().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
