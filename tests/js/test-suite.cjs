const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const fixtures = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'bindings', 'atomic-fixtures.json'), 'utf8'),
).fixtures;

function words(value) {
  if (!value) return [];
  return value.trim().split(/\s+/).sort();
}

function fixtureBoard(ffish, fixture) {
  return new ffish.Board(
    'atomic',
    fixture.fen || (fixture.position === 'startpos' ? '' : fixture.position?.replace(/^fen /, '')),
    fixture.chess960 || false,
  );
}

function pushFixture(board, fixture) {
  if (fixture.moves?.length) board.pushMoves(fixture.moves.join(' '));
}

function assertThrows(action, message) {
  let threw = false;
  try {
    action();
  } catch {
    threw = true;
  }
  assert.equal(threw, true, message);
}

function testFixture(ffish, fixture) {
  if (fixture.probe === 'target_contract') return;
  if (fixture.probe === 'validate_fen') {
    assert.equal(
      ffish.validateFen(fixture.fen, fixture.variant, fixture.chess960 || false),
      fixture.expected,
      fixture.id,
    );
    return;
  }
  if (fixture.probe === 'start_fen') {
    assert.equal(ffish.startingFen('atomic'), fixture.expected, fixture.id);
    return;
  }
  if (fixture.probe === 'captures_to_hand') {
    assert.equal(ffish.capturesToHand('atomic'), fixture.expected, fixture.id);
    return;
  }
  if (fixture.probe === 'two_boards') {
    assert.equal(ffish.twoBoards('atomic'), fixture.expected, fixture.id);
    return;
  }

  const board = fixtureBoard(ffish, fixture);
  try {
    switch (fixture.probe) {
      case 'perft':
        assert.equal(board.perft(fixture.depth), fixture.expected, fixture.id);
        break;
      case 'legal_moves':
        assert.deepEqual(words(board.legalMoves()), [...fixture.expected].sort(), fixture.id);
        assert.equal(board.numberLegalMoves(), fixture.expected.length, `${fixture.id}: count`);
        break;
      case 'is_capture':
        assert.equal(board.isCapture(fixture.move), fixture.expected, fixture.id);
        break;
      case 'get_san':
        assert.equal(board.sanMove(fixture.move), fixture.expected, fixture.id);
        break;
      case 'get_fen':
        pushFixture(board, fixture);
        assert.equal(board.fen(), fixture.expected, fixture.id);
        break;
      case 'gives_check':
        pushFixture(board, fixture);
        assert.equal(board.isCheck(), fixture.expected, fixture.id);
        assert.deepEqual(
          words(board.checkedPieces()),
          [...fixture.bindingExpected.checkedPieces].sort(),
          `${fixture.id}: checked pieces`,
        );
        break;
      case 'has_insufficient_material':
        assert.equal(board.hasInsufficientMaterial(true), fixture.expected[0], `${fixture.id}: white`);
        assert.equal(board.hasInsufficientMaterial(false), fixture.expected[1], `${fixture.id}: black`);
        assert.equal(
          board.isInsufficientMaterial(),
          fixture.expected[0] && fixture.expected[1],
          `${fixture.id}: aggregate`,
        );
        break;
      case 'game_result':
        pushFixture(board, fixture);
        assert.equal(board.result(), fixture.bindingExpected.javascript, fixture.id);
        assert.equal(board.isGameOver(), fixture.bindingExpected.isGameOver, `${fixture.id}: game over`);
        break;
      case 'is_immediate_game_end':
        pushFixture(board, fixture);
        assert.equal(board.result(), '1-0', fixture.id);
        assert.equal(board.isGameOver(), true, `${fixture.id}: game over`);
        break;
      case 'is_optional_game_end':
        pushFixture(board, fixture);
        assert.equal(board.result(false), '*', `${fixture.id}: unclaimed`);
        assert.equal(board.result(true), '1/2-1/2', `${fixture.id}: claimed`);
        assert.equal(board.isGameOver(true), true, `${fixture.id}: game over`);
        break;
      case 'lifecycle': {
        board.pushMoves(fixture.moves.join(' '));
        const expected = fixture.expected;
        assert.equal(board.fen(), expected.fen, `${fixture.id}: fen`);
        assert.equal(board.fen(true), expected.fen, `${fixture.id}: showPromoted`);
        assert.equal(board.fen(true, 99), expected.fen, `${fixture.id}: countStarted ignored`);
        assert.equal(board.turn(), expected.turn === 'white', `${fixture.id}: turn`);
        assert.equal(board.fullmoveNumber(), expected.fullmoveNumber, `${fixture.id}: fullmove`);
        assert.equal(board.halfmoveClock(), expected.halfmoveClock, `${fixture.id}: halfmove`);
        assert.equal(board.gamePly(), expected.gamePly, `${fixture.id}: ply`);
        assert.equal(board.is960(), expected.is960, `${fixture.id}: is960`);
        assert.equal(board.numberLegalMoves(), expected.legalMoveCount, `${fixture.id}: legal count`);
        assert.equal(board.moveStack(), expected.moveStack.join(' '), `${fixture.id}: stack`);
        board.pop();
        assert.equal(board.moveStack(), fixture.moves.slice(0, -1).join(' '), `${fixture.id}: pop stack`);
        assert.equal(board.fen(), expected.fenAfterPop, `${fixture.id}: pop fen`);
        board.reset();
        assert.equal(board.fen(), expected.fenAfterReset, `${fixture.id}: reset`);
        break;
      }
      default:
        assert.fail(`fixture ${fixture.id} has untested probe ${fixture.probe}`);
    }
  } finally {
    board.delete();
  }
}

function testNotationAndLifecycle(ffish) {
  const board = new ffish.Board('atomic');
  try {
    assert.equal(board.variant(), 'atomic');
    assert.equal(board.is960(), false);
    assert.equal(board.sanMove('g1f3', ffish.Notation.DEFAULT), 'Nf3');
    assert.equal(board.sanMove('g1f3', ffish.Notation.SAN), 'Nf3');
    assert.equal(board.sanMove('g1f3', ffish.Notation.LAN), 'Ng1-f3');
    assert.equal(board.pushSan('Ng1-f3', ffish.Notation.LAN), true);
    board.pop();
    assert.equal(board.moveStack(), '');

    board.push('e2e4');
    const initial = board.fen();
    assert.equal(board.variationSan('e7e5 g1f3 b8c6 f1c4'), '1...e5 2. Nf3 Nc6 3. Bc4');
    assert.equal(
      board.variationSan('e7e5 g1f3 b8c6 f1c4', ffish.Notation.LAN),
      '1...e7-e5 2. Ng1-f3 Nb8-c6 3. Bf1-c4',
    );
    assert.equal(
      board.variationSan('e7e5 g1f3 b8c6 f1c4', ffish.Notation.SAN, false),
      'e5 Nf3 Nc6 Bc4',
    );
    assert.equal(board.variationSan('e7e5 g1f3 b8c6 f1c7'), '');
    assert.equal(board.fen(), initial, 'variationSan must not mutate the board');

    const beforeInvalid = board.fen();
    assert.equal(board.push('q2q7'), false);
    assert.equal(board.fen(), beforeInvalid);
    assertThrows(() => board.pushMoves('e7e5 q2q7'), 'bulk UCI failure must throw');
    assert.equal(board.fen(), beforeInvalid, 'bulk UCI failure must be transactional');
    assertThrows(() => board.pushSanMoves('e5 NotAMove'), 'bulk SAN failure must throw');
    assert.equal(board.fen(), beforeInvalid, 'bulk SAN failure must be transactional');
  } finally {
    board.delete();
  }

  const empty = new ffish.Board('atomic');
  try {
    assertThrows(() => empty.pop(), 'empty pop must throw');
  } finally {
    empty.delete();
  }
}

function testRendering(ffish) {
  const board = new ffish.Board('atomic');
  try {
    assert.equal(
      board.toString(),
      [
        'r n b q k b n r',
        'p p p p p p p p',
        '. . . . . . . .',
        '. . . . . . . .',
        '. . . . . . . .',
        '. . . . . . . .',
        'P P P P P P P P',
        'R N B Q K B N R',
      ].join('\n'),
    );
    assert.match(board.toVerboseString(), /Fen: rnbqkbnr\/pppppppp/);
  } finally {
    board.delete();
  }
}

function testPgn(ffish) {
  const pgn = `[Event "Atomic fixture"]
[Variant "Atomic"]

1. e4 {main line} (1. d4 d5) e5 $1 2. Qh5!? a6 3. Qxf7# 1-0`;
  const game = ffish.readGamePGN(pgn);
  try {
    assert.deepEqual(words(game.headerKeys()), ['Event', 'Variant']);
    assert.equal(game.headers('Event'), 'Atomic fixture');
    assert.equal(game.headers('Variant'), 'Atomic');
    assert.equal(game.headers('Missing'), '');
    assert.equal(game.mainlineMoves(), 'e2e4 e7e5 d1h5 a7a6 h5f7');
  } finally {
    game.delete();
  }

  const pgn960 = `[Variant "Atomic960"]
[SetUp "1"]
[FEN "7k/8/8/8/8/8/8/1RK5 w Q - 0 1"]

1. O-O-O *`;
  const game960 = ffish.readGamePGN(pgn960);
  try {
    assert.equal(game960.mainlineMoves(), 'c1b1');
  } finally {
    game960.delete();
  }

  assertThrows(
    () => ffish.readGamePGN('[Variant "Crazyhouse"]\n\n1. e4 *'),
    'non-Atomic PGN must fail',
  );
  assertThrows(
    () => ffish.readGamePGN('[Variant "Atomic"]\n\n1. NotAMove *'),
    'invalid Atomic PGN must fail',
  );
}

function testContractsAndLifetime(ffish) {
  assert.equal(ffish.info(), "Atomic-Stockfish 1.0.3 JS/WASM");
  assert.equal(ffish.variants(), 'atomic');
  assert.equal(ffish.startingFen('atomic'), fixtures.find((f) => f.id === 'contract.start-fen').expected);
  assert.equal(ffish.capturesToHand('atomic'), false);
  assert.equal(ffish.twoBoards('atomic'), false);
  assert.equal(ffish.loadVariantConfig, undefined);

  ffish.setOption('UCI_Variant', 'atomic');
  ffish.setOption('Use NNUE', 'false');
  ffish.setOptionInt('Threads', 1);
  ffish.setOptionInt('Hash', 16);
  ffish.setOptionBool('UCI_Chess960', true);
  ffish.setOptionBool('Ponder', false);
  assertThrows(() => ffish.setOption('UCI_Variant', 'chess'), 'non-Atomic option must fail');
  assertThrows(() => ffish.setOptionInt('Threads', 0), 'invalid thread count must fail');
  assertThrows(() => new ffish.Board('chess'), 'non-Atomic Board must fail');

  const invalidFens = [
    '7k/8/8/8/8/8/8/K7 w - -',
    '7k/8/8/8/8/8/8/K7 w - - not-a-number 1',
  ];
  const liveBoardsBeforeInvalidFen = ffish.debugLiveBoards();
  invalidFens.forEach((fen) => {
    assertThrows(() => new ffish.Board('atomic', fen), 'invalid FEN construction must fail');
    assert.equal(
      ffish.debugLiveBoards(),
      liveBoardsBeforeInvalidFen,
      'failed FEN construction must not commit a Board',
    );
  });

  const board = new ffish.Board('atomic');
  try {
    const original = board.fen();
    assertThrows(() => board.setFen('not a fen'), 'invalid FEN must throw');
    assert.equal(board.fen(), original, 'setFen failure must be transactional');
    invalidFens.forEach((fen) => {
      assertThrows(() => board.setFen(fen), 'strictly invalid FEN must throw');
      assert.equal(board.fen(), original, 'invalid FEN must not replace the current position');
    });
  } finally {
    board.delete();
  }

  const heapBytesBefore = ffish.wasmHeapBytes();
  const liveBoardsBefore = ffish.debugLiveBoards();
  for (let index = 0; index < 5000; index += 1) {
    const disposable = new ffish.Board('atomic');
    disposable.push('e2e4');
    disposable.delete();
  }
  assert.equal(ffish.debugLiveBoards(), liveBoardsBefore, 'Board.delete must release every C++ Board');
  assert.equal(
    ffish.wasmHeapBytes(),
    heapBytesBefore,
    'repeated create/delete must not grow the WASM heap',
  );

  const simultaneous = Array.from({ length: 64 }, () => new ffish.Board('atomic'));
  try {
    assert.equal(ffish.debugLiveBoards(), liveBoardsBefore + 64, 'all simultaneous Boards are live');
    simultaneous.forEach((candidate, index) => {
      if (index % 2 === 0) candidate.push('e2e4');
      assert.equal(candidate.turn(), index % 2 !== 0, `independent board ${index}`);
    });
  } finally {
    simultaneous.forEach((candidate) => candidate.delete());
  }
  assert.equal(ffish.debugLiveBoards(), liveBoardsBefore, 'simultaneous Boards must all be released');
}

async function runSuite(ffish, surface) {
  for (const fixture of fixtures) testFixture(ffish, fixture);
  testNotationAndLifecycle(ffish);
  testRendering(ffish);
  testPgn(ffish);
  testContractsAndLifetime(ffish);
  console.log(`${surface}: ${fixtures.length} fixtures and binding lifecycle checks passed`);
}

module.exports = { runSuite };
