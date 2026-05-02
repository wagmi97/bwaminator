/**
 * Multi-relayer race. Submit the same signed v0 transaction to N landing
 * services in parallel; whichever lands first wins. The others harmlessly
 * see "already processed" and drop.
 *
 * Each relayer takes its own tip, so we pay 4-5x the base tip — cheap relative
 * to the cost of missing a deploy. With Solana's slot of ~400ms, the variance
 * between relayers can be 1-3 slots, so racing typically saves 400-1200ms.
 *
 * Note: each service has its own tip-account requirement. Build the tx with
 * one tip transfer per service ALREADY embedded — they'll all see the tip
 * meant for them; landing a tx with extra tips to other services is fine.
 */
import { PublicKey, SystemProgram, TransactionInstruction } from "@solana/web3.js";

export interface Relayer {
  name: string;
  tipAccount: PublicKey;
  send: (rawTx: Uint8Array) => Promise<string>;
}

export function buildRelayers(): Relayer[] {
  const out: Relayer[] = [];

  if (process.env.NEXTBLOCK_API_KEY) {
    out.push({
      name: "nextblock",
      tipAccount: new PublicKey("NextbLoCkVtMGcV47JzewQdvBpLqT9TxQFozQkN98pE"),
      send: async (rawTx) => {
        const r = await fetch(`${process.env.NEXTBLOCK_ENDPOINT}/api/v2/submit`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: process.env.NEXTBLOCK_API_KEY! },
          body: JSON.stringify({
            transaction: { content: Buffer.from(rawTx).toString("base64") },
            frontRunningProtection: true,
          }),
        });
        const j = (await r.json()) as { signature: string };
        return j.signature;
      },
    });
  }

  if (process.env.JITO_BLOCK_ENGINE_URL) {
    // Jito has 8 tip accounts; pick one round-robin to spread tips
    const jitoTips = [
      "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
      "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
      "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
      "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
      "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
      "ADuUkR4vqLUMWXxW9gh6D6L8pivKeVBBXYJYnD4yL5uA",
      "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
      "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
    ];
    out.push({
      name: "jito",
      tipAccount: new PublicKey(jitoTips[Math.floor(Math.random() * jitoTips.length)]),
      send: async (rawTx) => {
        const r = await fetch(`${process.env.JITO_BLOCK_ENGINE_URL}/api/v1/transactions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "sendTransaction",
            params: [Buffer.from(rawTx).toString("base64"), { encoding: "base64" }],
          }),
        });
        const j = (await r.json()) as { result: string };
        return j.result;
      },
    });
  }

  if (process.env.ZERO_SLOT_KEY) {
    out.push({
      name: "0slot",
      tipAccount: new PublicKey("4HiwLEP2Bzqj3hM2ENxJuzhcPCdsafwiet3oGkMkuQY4"),
      send: async (rawTx) => {
        const r = await fetch(`${process.env.ZERO_SLOT_ENDPOINT}/?api-key=${process.env.ZERO_SLOT_KEY}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "sendTransaction",
            params: [Buffer.from(rawTx).toString("base64"), { encoding: "base64", skipPreflight: true }],
          }),
        });
        const j = (await r.json()) as { result: string };
        return j.result;
      },
    });
  }

  if (process.env.HELIUS_SENDER_KEY) {
    out.push({
      name: "helius-sender",
      tipAccount: new PublicKey("4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE"),
      send: async (rawTx) => {
        const r = await fetch(`${process.env.HELIUS_SENDER_ENDPOINT}/fast?api-key=${process.env.HELIUS_SENDER_KEY}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "sendTransaction",
            params: [Buffer.from(rawTx).toString("base64"), { encoding: "base64", skipPreflight: true }],
          }),
        });
        const j = (await r.json()) as { result: string };
        return j.result;
      },
    });
  }

  return out;
}

/** Build N tip transfer instructions, one per relayer, payable from the same
 *  payer. Add these to the deploy tx so whichever relayer wins gets paid. */
export function buildTipInstructions(
  payer: PublicKey,
  relayers: Relayer[],
  tipLamportsPerRelayer: number
): TransactionInstruction[] {
  return relayers.map((r) =>
    SystemProgram.transfer({
      fromPubkey: payer,
      toPubkey: r.tipAccount,
      lamports: tipLamportsPerRelayer,
    })
  );
}

/** Submit the same tx to every relayer; resolve with the first non-error
 *  signature; the rest are abandoned (their fetches keep going but we don't
 *  care). */
export async function raceSend(rawTx: Uint8Array, relayers: Relayer[]): Promise<{ via: string; signature: string; raced: { name: string; ms: number }[] }> {
  const t0 = performance.now();
  const traces: { name: string; ms: number }[] = [];

  const promises = relayers.map((r) =>
    r.send(rawTx)
      .then((sig) => ({ name: r.name, sig }))
      .catch((e) => ({ name: r.name, sig: null, err: e })),
  );

  // Resolve when any one returns a non-null sig
  return await new Promise((resolve, reject) => {
    let resolved = false;
    let pending = relayers.length;
    promises.forEach((p) =>
      p.then((res) => {
        traces.push({ name: res.name, ms: performance.now() - t0 });
        pending--;
        if (!resolved && (res as any).sig) {
          resolved = true;
          resolve({ via: res.name, signature: (res as any).sig as string, raced: traces });
        } else if (pending === 0 && !resolved) {
          reject(new Error("all relayers failed"));
        }
      }),
    );
  });
}
