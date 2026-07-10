export interface ModuleOptions {
  locateFile?: (file: string, prefix: string) => string;
  onAbort?: (status: string | number) => void;
  onRuntimeInitialized?: () => void;
  print?: (text: string) => void;
  printErr?: (text: string) => void;
  wasmMemory?: WebAssembly.Memory;
}

export interface BoardConstructor {
  new (uciVariant?: string, fen?: string, is960?: boolean): Board;
}

export interface Game {
  delete(): void;
  headerKeys(): string;
  headers(item: string): string;
  mainlineMoves(): string;
}

export interface Board {
  delete(): void;
  legalMoves(): string;
  legalMovesSan(): string;
  numberLegalMoves(): number;
  push(uciMove: string): boolean;
  pushSan(move: string, notation?: Notation): boolean;
  pop(): void;
  reset(): void;
  is960(): boolean;
  fen(showPromoted?: boolean, countStarted?: number): string;
  setFen(fen: string): void;
  sanMove(uciMove: string, notation?: Notation): string;
  variationSan(uciMoves: string, notation?: Notation, moveNumbers?: boolean): string;
  turn(): boolean;
  fullmoveNumber(): number;
  halfmoveClock(): number;
  gamePly(): number;
  hasInsufficientMaterial(white: boolean): boolean;
  isInsufficientMaterial(): boolean;
  isGameOver(claimDraw?: boolean): boolean;
  result(claimDraw?: boolean): string;
  checkedPieces(): string;
  isCheck(): boolean;
  isCapture(uciMove: string): boolean;
  givesCheck(uciMove: string): boolean;
  moveStack(): string;
  pushMoves(uciMoves: string): void;
  pushSanMoves(moves: string, notation?: Notation): void;
  perft(depth: number): number;
  toString(): string;
  toVerboseString(): string;
  variant(): "atomic";
}

export enum Notation {
  DEFAULT,
  SAN,
  LAN,
}

export enum Termination {
  ONGOING,
  ATOMIC_EXPLOSION,
  CHECKMATE,
  STALEMATE,
  INSUFFICIENT_MATERIAL,
  FIFTY_MOVE_RULE,
  THREEFOLD_REPETITION,
}

export interface AtomicStockfish {
  Board: BoardConstructor;
  Game: unknown;
  Notation: typeof Notation;
  Termination: typeof Termination;
  info(): string;
  setOption(name: string, value: string): void;
  setOptionInt(name: string, value: number): void;
  setOptionBool(name: string, value: boolean): void;
  readGamePGN(pgn: string): Game;
  variants(): "atomic";
  debugLiveBoards(): number;
  wasmHeapBytes(): number;
  twoBoards(uciVariant: string): false;
  capturesToHand(uciVariant: string): false;
  startingFen(uciVariant: string): string;
  validateFen(fen: string, uciVariant?: string, chess960?: boolean): number;
}

export default function createAtomicStockfish(options?: ModuleOptions): Promise<AtomicStockfish>;
