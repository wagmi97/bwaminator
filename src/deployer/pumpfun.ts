/**
 * Pumpfun program ABI bindings — reconstructed from the OFFICIAL IDL at
 *   github.com/.../pump-public-docs/idl/pump.json
 *
 * **POLICY: WE NEVER USE MAYHEM MODE.** is_mayhem_mode is hard-coded false on
 * every create_v2 we build. The 5 Mayhem accounts (mayhem_program_id,
 * global_params, sol_vault, mayhem_state, mayhem_token_vault) remain in the
 * account list because the program's account validation requires them, but
 * with the bool false the program does not invoke any Mayhem CPI or write
 * Mayhem state for our coin. Trade fee recipients use the standard pump
 * fee_recipients pool, NOT the Mayhem fee recipients.
 *
 * Two on-chain mechanisms we still rely on:
 *
 *   1. cashback                Creator fee redirected to buyers based on
 *                              their UserVolumeAccumulator. OptionBool arg
 *                              of create_v2; toggled per coin.
 *
 *   2. token incentives        ("agent mode" / buybacks). Pump.fun ADMIN
 *                              allocates daily token supply to a coin via
 *                              `admin_update_token_incentives`. Users with
 *                              volume on the coin claim their pro-rata share
 *                              via `claim_token_incentives`. There is NO
 *                              public on-chain entrypoint for a creator to
 *                              enable this — pump.fun curates which coins
 *                              get incentives, presumably surfaced from
 *                              metadata `category: "agent"` tags.
 *
 * Deploy strategy mapping:
 *   - "meme"  → create_v2(mayhem=false, cashback=Some(true))   buy(track_volume=true)
 *               + metadata `category: "meme"`
 *   - "agent" → create_v2(mayhem=false, cashback=Some(false))  buy(track_volume=true)
 *               + metadata `category: "agent"`
 *               + post-deploy: monitor for `globalIncentiveTokenAccount` to
 *                 be funded; once funded, periodically call
 *                 `claim_token_incentives` to harvest buybacks. We always set
 *                 track_volume=true on our snipe so we accrue claim eligibility.
 */
import {
  PublicKey,
  TransactionInstruction,
  SystemProgram,
} from "@solana/web3.js";
import BN from "bn.js";

// ---- Program IDs (verified from on-chain + docs) ----
export const PUMP_PROGRAM = new PublicKey("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");
export const PUMP_FEE_PROGRAM = new PublicKey("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ");
export const PUMP_AMM_PROGRAM = new PublicKey("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
export const TOKEN_2022_PROGRAM = new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
export const TOKEN_LEGACY_PROGRAM = new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
export const ASSOCIATED_TOKEN_PROGRAM = new PublicKey("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");

// Mayhem program + static accounts. KEPT ONLY because create_v2's account
// validation requires them as inert slots when is_mayhem_mode=false. We
// never invoke Mayhem.
export const MAYHEM_PROGRAM = new PublicKey("MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e");
export const MAYHEM_GLOBAL_PARAMS = new PublicKey("13ec7XdrjF3h3YcqBTFDSReRcUFwbCnJaAQspM4j6DDJ");
export const MAYHEM_SOL_VAULT = new PublicKey("BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s");

// Standard pump fee recipients (from the Global account; pick one randomly
// per buy/sell to spread tip volume across them). Use these instead of
// MAYHEM_FEE_RECIPIENTS because we deploy with is_mayhem_mode=false.
export const STANDARD_FEE_RECIPIENT = new PublicKey(
  "62qc2CNXwrYqQScmEdiZFFAnJR262PxWEuNQtxfafNgV"
);
export const FEE_RECIPIENTS_POOL = [
  STANDARD_FEE_RECIPIENT,
  new PublicKey("7VtfL8fvgNfhz17qKRMjzQEXgbdpnHHHQRh54R9jP2RJ"),
  new PublicKey("7hTckgnGnLQR6sdH7YkqFTAA7VwTfYFaZ6EhEsU3saCX"),
  new PublicKey("9rPYyANsfQZw3DnDmKE3YCQF5E8oD89UXoHn9JFEhJUz"),
  new PublicKey("AVmoTthdrX6tKt4nDjco2D775W2YK3sDhxPcMmzUAmTY"),
  new PublicKey("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM"),
  new PublicKey("FWsW1xNtWscwNmKv6wVsU1iTzRN6wmmk3MjxRP5tT7hz"),
  new PublicKey("G5UZAVbAf46s7cKWoyKu8kYTip9DGTpbLZ2qa9Aq69dP"),
];

export function pickFeeRecipient(): PublicKey {
  return FEE_RECIPIENTS_POOL[Math.floor(Math.random() * FEE_RECIPIENTS_POOL.length)];
}

// FluxBeam-style anti-MEV vanity flag pubkeys (read by friendly relayers)
export const DONT_BUNDLE_FLAG = new PublicKey("dontbund1e11111111111F1UXBEAM11111111r4pfou");
export const DONT_FRONT_FLAG = new PublicKey("jitodontfront11111111F1UXBEAM11111111nQRVsr");

// Anchor instruction discriminators (from IDL)
const DISC_CREATE_V2 = Buffer.from([214, 144, 76, 236, 95, 139, 49, 180]);
const DISC_BUY_EXACT_SOL_IN = Buffer.from([56, 252, 116, 8, 158, 223, 205, 95]);
const DISC_BUY = Buffer.from([102, 6, 61, 18, 1, 218, 235, 234]);
const DISC_SELL = Buffer.from([51, 230, 133, 164, 1, 127, 131, 173]);
const DISC_CLAIM_TOKEN_INCENTIVES = Buffer.from([16, 4, 71, 28, 204, 1, 40, 27]);

// FeeConfig PDA seed const (from IDL: ["fee_config", <32 bytes>])
const FEE_CONFIG_SEED_TAIL = Buffer.from([
  1, 86, 224, 246, 147, 102, 90, 207, 68, 219, 21, 104, 191, 23, 91, 170,
  81, 137, 203, 151, 245, 210, 255, 59, 101, 93, 43, 182, 253, 109, 24, 176,
]);

// ---- Encoding helpers ----
function encodeStr(s: string): Buffer {
  const body = Buffer.from(s, "utf8");
  const len = Buffer.alloc(4);
  len.writeUInt32LE(body.length, 0);
  return Buffer.concat([len, body]);
}

/** OptionBool: 1 byte tag (0=None, 1=Some) + optional 1-byte bool payload. */
function encodeOptionBool(v: boolean | null): Buffer {
  if (v === null) return Buffer.from([0]);
  return Buffer.from([1, v ? 1 : 0]);
}

// ---- PDA derivation ----
export function pumpfunPdas(mint: PublicKey) {
  const [global_] = PublicKey.findProgramAddressSync([Buffer.from("global")], PUMP_PROGRAM);
  const [mintAuthority] = PublicKey.findProgramAddressSync(
    [Buffer.from("mint-authority")],
    PUMP_PROGRAM
  );
  const [bondingCurve] = PublicKey.findProgramAddressSync(
    [Buffer.from("bonding-curve"), mint.toBuffer()],
    PUMP_PROGRAM
  );
  const [eventAuthority] = PublicKey.findProgramAddressSync(
    [Buffer.from("__event_authority")],
    PUMP_PROGRAM
  );
  const [globalVolumeAccumulator] = PublicKey.findProgramAddressSync(
    [Buffer.from("global_volume_accumulator")],
    PUMP_PROGRAM
  );
  const [feeConfig] = PublicKey.findProgramAddressSync(
    [Buffer.from("fee_config"), FEE_CONFIG_SEED_TAIL],
    PUMP_FEE_PROGRAM
  );
  return { global_, mintAuthority, bondingCurve, eventAuthority, globalVolumeAccumulator, feeConfig };
}

export function userVolumeAccumulator(user: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("user_volume_accumulator"), user.toBuffer()],
    PUMP_PROGRAM
  );
  return pda;
}

export function creatorVault(creator: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("creator-vault"), creator.toBuffer()],
    PUMP_PROGRAM
  );
  return pda;
}

export function mayhemPdas(mint: PublicKey) {
  const [mayhemState] = PublicKey.findProgramAddressSync(
    [Buffer.from("mayhem-state"), mint.toBuffer()],
    MAYHEM_PROGRAM
  );
  return { mayhemState };
}

/** Token-2022 ATA derivation. Token-2022 owner replaces legacy SPL token in
 *  `getAssociatedTokenAddress`. */
export function token2022Ata(owner: PublicKey, mint: PublicKey): PublicKey {
  const [ata] = PublicKey.findProgramAddressSync(
    [owner.toBuffer(), TOKEN_2022_PROGRAM.toBuffer(), mint.toBuffer()],
    ASSOCIATED_TOKEN_PROGRAM
  );
  return ata;
}

// ---- create_v2 ----

export type DeployStrategy = "meme" | "agent";

export interface CreateV2Args {
  payer: PublicKey;
  mint: PublicKey;
  creator: PublicKey;
  name: string;
  symbol: string;
  uri: string;
  /** "meme" → cashback ON; "agent" → cashback OFF (creator keeps fees). */
  strategy: DeployStrategy;
}

/** Build the CreateV2 instruction. Account order matches IDL exactly.
 *  Args:
 *    name, symbol, uri (Strings)
 *    creator (Pubkey)
 *    is_mayhem_mode (bool)               — always true; mayhem is the only path
 *    is_cashback_enabled (OptionBool)    — Some(true) for memes, Some(false) for agents
 */
export function buildCreateV2(args: CreateV2Args): TransactionInstruction {
  const pdas = pumpfunPdas(args.mint);
  const mayhem = mayhemPdas(args.mint);
  const associatedBondingCurve = token2022Ata(pdas.bondingCurve, args.mint);
  const mayhemTokenVault = token2022Ata(MAYHEM_SOL_VAULT, args.mint);

  const data = Buffer.concat([
    DISC_CREATE_V2,
    encodeStr(args.name),
    encodeStr(args.symbol),
    encodeStr(args.uri),
    args.creator.toBuffer(),
    Buffer.from([0]),                                  // is_mayhem_mode = FALSE (policy)
    encodeOptionBool(args.strategy === "meme"),        // is_cashback_enabled
  ]);

  // Account order MATCHES IDL exactly (16 accounts).
  const keys = [
    { pubkey: args.mint, isSigner: true, isWritable: true },
    { pubkey: pdas.mintAuthority, isSigner: false, isWritable: false },
    { pubkey: pdas.bondingCurve, isSigner: false, isWritable: true },
    { pubkey: associatedBondingCurve, isSigner: false, isWritable: true },
    { pubkey: pdas.global_, isSigner: false, isWritable: false },
    { pubkey: args.payer, isSigner: true, isWritable: true },
    { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    { pubkey: TOKEN_2022_PROGRAM, isSigner: false, isWritable: false },
    { pubkey: ASSOCIATED_TOKEN_PROGRAM, isSigner: false, isWritable: false },
    { pubkey: MAYHEM_PROGRAM, isSigner: false, isWritable: true },
    { pubkey: MAYHEM_GLOBAL_PARAMS, isSigner: false, isWritable: false },
    { pubkey: MAYHEM_SOL_VAULT, isSigner: false, isWritable: true },
    { pubkey: mayhem.mayhemState, isSigner: false, isWritable: true },
    { pubkey: mayhemTokenVault, isSigner: false, isWritable: true },
    { pubkey: pdas.eventAuthority, isSigner: false, isWritable: false },
    { pubkey: PUMP_PROGRAM, isSigner: false, isWritable: false },
  ];

  return new TransactionInstruction({ programId: PUMP_PROGRAM, keys, data });
}

// ---- buy_exact_sol_in ----

export interface BuyExactSolInArgs {
  payer: PublicKey;
  mint: PublicKey;
  /** The bonding curve's `creator` field — set to the deployer for fresh creates. */
  creator: PublicKey;
  spendableSolIn: BN;
  minTokensOut: BN;
  /** track_volume: Some(true) = enable cashback accrual, Some(false) = disable.
   *  None = follow the coin's default (use this when you don't care). */
  trackVolume: boolean | null;
  /** Cashback coins: pass `true` so we add user_volume_accumulator at the
   *  remaining-account index. Honored by the program when the coin is a
   *  cashback coin; ignored otherwise. */
  isCashbackCoin: boolean;
}

export function buildBuyExactSolIn(args: BuyExactSolInArgs): TransactionInstruction {
  const pdas = pumpfunPdas(args.mint);
  const associatedBondingCurve = token2022Ata(pdas.bondingCurve, args.mint);
  const associatedUser = token2022Ata(args.payer, args.mint);
  const userVolume = userVolumeAccumulator(args.payer);
  const cVault = creatorVault(args.creator);

  const data = Buffer.concat([
    DISC_BUY_EXACT_SOL_IN,
    args.spendableSolIn.toArrayLike(Buffer, "le", 8),
    args.minTokensOut.toArrayLike(Buffer, "le", 8),
    encodeOptionBool(args.trackVolume),
  ]);

  // Account order MATCHES IDL exactly (16 accounts). fee_recipient slot uses
  // the STANDARD pump fee recipient pool because is_mayhem_mode=false.
  const keys = [
    { pubkey: pdas.global_, isSigner: false, isWritable: false },
    { pubkey: pickFeeRecipient(), isSigner: false, isWritable: true },
    { pubkey: args.mint, isSigner: false, isWritable: false },
    { pubkey: pdas.bondingCurve, isSigner: false, isWritable: true },
    { pubkey: associatedBondingCurve, isSigner: false, isWritable: true },
    { pubkey: associatedUser, isSigner: false, isWritable: true },
    { pubkey: args.payer, isSigner: true, isWritable: true },
    { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    { pubkey: TOKEN_2022_PROGRAM, isSigner: false, isWritable: false },
    { pubkey: cVault, isSigner: false, isWritable: true },
    { pubkey: pdas.eventAuthority, isSigner: false, isWritable: false },
    { pubkey: PUMP_PROGRAM, isSigner: false, isWritable: false },
    { pubkey: pdas.globalVolumeAccumulator, isSigner: false, isWritable: false },
    { pubkey: userVolume, isSigner: false, isWritable: true },
    { pubkey: pdas.feeConfig, isSigner: false, isWritable: false },
    { pubkey: PUMP_FEE_PROGRAM, isSigner: false, isWritable: false },
  ];

  return new TransactionInstruction({ programId: PUMP_PROGRAM, keys, data });
}

/** Pumpfun fronts a "DON'T BUNDLE / DON'T FRONT" hint by passing flag pubkeys
 *  as account_metas to a no-op ComputeBudget instruction. */
export function antiMevFlagAccounts() {
  return [
    { pubkey: DONT_BUNDLE_FLAG, isSigner: false, isWritable: false },
    { pubkey: DONT_FRONT_FLAG, isSigner: false, isWritable: false },
  ];
}

// ---- claim_token_incentives ("agent mode" buyback harvest) ----

/** Derive the global_incentive_token_account ATA (Token-2022) for a given
 *  mint. This is where pump.fun admin deposits the daily incentive token
 *  supply for an "agent mode" coin. */
export function globalIncentiveTokenAccount(mint: PublicKey): PublicKey {
  const [globalVol] = PublicKey.findProgramAddressSync(
    [Buffer.from("global_volume_accumulator")],
    PUMP_PROGRAM
  );
  return token2022Ata(globalVol, mint);
}

export interface ClaimTokenIncentivesArgs {
  user: PublicKey;
  mint: PublicKey;
  payer: PublicKey;
}

/** Build the claim_token_incentives instruction. Run this periodically (e.g.
 *  daily cron) for every "agent" coin we hold a position in. The on-chain
 *  formula releases tokens proportional to the user's volume vs total
 *  volume on the coin, capped by what's been deposited. Returns 0 tokens
 *  if pump.fun hasn't enabled incentives for this coin yet — safe to call
 *  speculatively. */
export function buildClaimTokenIncentives(
  args: ClaimTokenIncentivesArgs
): TransactionInstruction {
  const pdas = pumpfunPdas(args.mint);
  const userAta = token2022Ata(args.user, args.mint);
  const userVolume = userVolumeAccumulator(args.user);
  const incentiveAcct = globalIncentiveTokenAccount(args.mint);

  const data = DISC_CLAIM_TOKEN_INCENTIVES; // no args

  // Account order MATCHES IDL (12 accounts).
  const keys = [
    { pubkey: args.user, isSigner: false, isWritable: false },
    { pubkey: userAta, isSigner: false, isWritable: true },
    { pubkey: pdas.globalVolumeAccumulator, isSigner: false, isWritable: false },
    { pubkey: incentiveAcct, isSigner: false, isWritable: true },
    { pubkey: userVolume, isSigner: false, isWritable: true },
    { pubkey: args.mint, isSigner: false, isWritable: false },
    { pubkey: TOKEN_2022_PROGRAM, isSigner: false, isWritable: false },
    { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
    { pubkey: ASSOCIATED_TOKEN_PROGRAM, isSigner: false, isWritable: false },
    { pubkey: pdas.eventAuthority, isSigner: false, isWritable: false },
    { pubkey: PUMP_PROGRAM, isSigner: false, isWritable: false },
    { pubkey: args.payer, isSigner: true, isWritable: true },
  ];

  return new TransactionInstruction({ programId: PUMP_PROGRAM, keys, data });
}
