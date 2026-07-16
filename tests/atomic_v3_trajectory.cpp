/*
  Focused AtomicNNUEV3 trajectory and partition tests.
*/

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#include "api/atomic_outcome.h"
#include "attacks.h"
#include "bitboard.h"
#include "data/atomic_v3_trajectory.h"
#include "data/training_data_generator.h"
#include "position.h"
#include "tt.h"
#include "uci_move.h"

using namespace Stockfish;
using namespace Stockfish::Data;

// These focused tests never pass a TT to Position::do_move(). Debug builds do
// not optimize the guarded prefetch away, so provide the same explicit test
// dependency used by the other Position-backed codec units.
TTEntry* TranspositionTable::first_entry(Key) const { return nullptr; }

namespace {

int Failures = 0;

void expect(bool condition, std::string_view id, std::string_view detail) {
    if (!condition)
    {
        ++Failures;
        std::cerr << "FAIL " << id << ": " << detail << '\n';
    }
}

AtomicV3StopReason reason_for(const Atomic::Outcome& outcome) {
    switch (outcome.termination)
    {
    case Atomic::Termination::AtomicExplosion :
        return AtomicV3StopReason::ATOMIC_EXPLOSION;
    case Atomic::Termination::Checkmate :
        return AtomicV3StopReason::CHECKMATE;
    case Atomic::Termination::Stalemate :
        return AtomicV3StopReason::STALEMATE;
    case Atomic::Termination::InsufficientMaterial :
        return AtomicV3StopReason::INSUFFICIENT_MATERIAL;
    case Atomic::Termination::FiftyMoveRule :
        return AtomicV3StopReason::FIFTY_MOVE_RULE;
    case Atomic::Termination::ThreefoldRepetition :
        return AtomicV3StopReason::THREEFOLD_REPETITION;
    case Atomic::Termination::Ongoing :
        return AtomicV3StopReason::MAXIMUM_PLY_DRAW;
    }
    return AtomicV3StopReason::MAXIMUM_PLY_DRAW;
}

AtomicV3Trajectory make_trajectory(std::string                     rootFen,
                                   bool                            atomic960,
                                   const std::vector<std::string>& moves,
                                   bool                            retainEveryPly = true) {
    AtomicV3Trajectory trajectory;
    trajectory.rootFen   = std::move(rootFen);
    trajectory.atomic960 = atomic960;

    StateInfo  rootState{};
    Position   position;
    const auto setError = position.set(trajectory.rootFen, atomic960, &rootState);
    if (setError)
        return trajectory;
    trajectory.rootFen = position.fen();
    (void) encode_atomic_bin_v2_position(position, trajectory.rootPosition);

    std::vector<StateInfo> states(moves.size());
    for (usize ply = 0; ply < moves.size(); ++ply)
    {
        const Move move = UCI::to_move(position, moves[ply]);
        if (move == Move::none())
            return trajectory;
        u32 wire = 0;
        (void) encode_atomic_bin_v2_move(move, wire);
        trajectory.playedMoves.push_back(wire);
        if (retainEveryPly)
            trajectory.samples.push_back(
              {position.fen(), 12, move, std::int64_t(ply), 0,
               atomic960 ? TRAINING_DATA_CHESS960 : NO_TRAINING_DATA_FLAGS});
        position.do_move(move, states[ply], nullptr);
    }

    const auto outcome        = Atomic::outcome(position, true, 0);
    trajectory.stopReason     = reason_for(outcome);
    trajectory.terminalResult = !outcome.winner ? 0 : (*outcome.winner == WHITE ? 1 : -1);
    for (auto& sample : trajectory.samples)
    {
        StateInfo sampleState{};
        Position  samplePosition;
        (void) samplePosition.set(sample.fen, atomic960, &sampleState);
        sample.result = trajectory.terminalResult == 0
                        ? 0
                        : (samplePosition.side_to_move() == WHITE ? trajectory.terminalResult
                                                                  : -trajectory.terminalResult);
    }
    return trajectory;
}

DataResult validate_fixture(const AtomicV3Trajectory& trajectory) {
    return validate_atomic_v3_trajectory(trajectory, u32(trajectory.playedMoves.size()));
}

void test_semantic_fixtures() {
    struct Fixture {
        std::string_view id;
        std::string_view fen;
        bool             atomic960;
        std::string_view move;
    };
    constexpr std::array<Fixture, 8> Fixtures = {{
      {"quiet", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false, "g1f3"},
      {"capture-explosion", "7k/8/8/2pBn3/3r4/2PQN3/8/K7 w - - 0 1", false, "d3d4"},
      {"en-passant", "7k/8/2N1b3/2ppP3/8/8/8/K7 w - d6 0 2", false, "e5d6"},
      {"promotion", "k5br/6P1/8/8/8/8/8/K7 w - - 0 1", false, "g7h8q"},
      {"castling", "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1", false, "e1g1"},
      {"atomic960-castling", "7k/8/8/8/8/8/8/1RK5 w Q - 0 1", true, "c1b1"},
      {"terminal-explosion", "7k/7R/8/8/8/8/8/K7 w - - 0 1", false, "h7h8"},
      {"terminal-checkmate", "B7/Rk6/8/8/8/8/7Q/4K3 w - - 0 1", false, "h2b8"},
    }};

    for (const auto& fixture : Fixtures)
    {
        auto trajectory =
          make_trajectory(std::string(fixture.fen), fixture.atomic960, {std::string(fixture.move)});
        const auto valid = validate_fixture(trajectory);
        expect(bool(valid), fixture.id, valid.message);
    }
}

void test_mutations() {
    const auto original = make_trajectory(
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false, {"g1f3", "g8f6"});
    expect(bool(validate_fixture(original)), "mutation.original", "fixture invalid");

    auto mutated = original;
    mutated.playedMoves[0] |= 1U << 31;
    expect(!validate_fixture(mutated), "mutation.move-reserved", "reserved move accepted");

    mutated                = original;
    mutated.samples[0].fen = "8/8/8/8/8/8/8/8 w - - 0 1";
    expect(!validate_fixture(mutated), "mutation.sample-position",
           "mismatched sample position accepted");

    mutated                   = original;
    mutated.samples[0].result = 1;
    expect(!validate_fixture(mutated), "mutation.result", "wrong result accepted");

    mutated            = original;
    mutated.stopReason = AtomicV3StopReason::EVALUATION_RESIGNATION;
    expect(!validate_fixture(mutated), "mutation.resignation", "resignation stop accepted");

    mutated            = original;
    mutated.stopReason = AtomicV3StopReason::SCORE_DRAW_ADJUDICATION;
    expect(!validate_fixture(mutated), "mutation.score-draw", "score draw stop accepted");

    auto ignoredInsufficient = make_trajectory("7k/8/8/8/8/8/8/K7 w - - 0 1", false, {"a1a2"});
    ignoredInsufficient.stopReason             = AtomicV3StopReason::MAXIMUM_PLY_DRAW;
    ignoredInsufficient.terminalResult         = 0;
    ignoredInsufficient.samples[0].result      = 0;
    ignoredInsufficient.adjudicateInsufficient = false;
    expect(bool(validate_fixture(ignoredInsufficient)), "policy.ignore-insufficient",
           "disabled insufficient-material policy was rejected");
    ignoredInsufficient.adjudicateInsufficient = true;
    expect(!validate_fixture(ignoredInsufficient), "policy.enforce-insufficient",
           "enabled insufficient-material terminal was ignored");

    expect(!validate_atomic_v3_trajectory(original, 3), "mutation.maximum-ply-binding",
           "maximum-ply stop was accepted against a different configured write_max_ply");

    auto postExplosion = make_trajectory("7k/7R/8/8/8/8/8/K7 w - - 0 1", false, {"h7h8"});
    postExplosion.playedMoves.push_back(postExplosion.playedMoves.front());
    expect(!validate_atomic_v3_trajectory(postExplosion, 2), "mutation.post-explosion",
           "move continuation after Atomic explosion was accepted");

    auto fifty = make_trajectory("7k/7r/8/8/8/8/R7/K7 w - - 99 1", false, {"a1b1"});
    expect(fifty.stopReason == AtomicV3StopReason::FIFTY_MOVE_RULE && bool(validate_fixture(fifty)),
           "terminal.fifty", "fifty-move terminal fixture was not accepted");
    fifty.playedMoves.push_back(fifty.playedMoves.front());
    expect(!validate_atomic_v3_trajectory(fifty, 2), "mutation.post-fifty",
           "move continuation after fifty-move terminal was accepted");

    auto threefold =
      make_trajectory("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", false,
                      {"g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8"});
    expect(threefold.stopReason == AtomicV3StopReason::THREEFOLD_REPETITION
             && bool(validate_fixture(threefold)),
           "terminal.threefold", "threefold terminal fixture was not accepted");
    threefold.playedMoves.push_back(threefold.playedMoves.front());
    expect(!validate_atomic_v3_trajectory(threefold, 9), "mutation.post-threefold",
           "move continuation after threefold terminal was accepted");
}

void test_feature_identity_and_external_sort() {
    auto trajectory = make_trajectory("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                                      false, {"g1f3", "g8f6"});
    AtomicV3FeatureInputKey first{}, relabeled{}, changed{};
    auto                    keyed = atomic_v3_feature_input_key(trajectory.samples.front(), first);
    expect(bool(keyed), "feature.key", keyed.message);

    auto labelOnly   = trajectory.samples.front();
    labelOnly.score  = -1234;
    labelOnly.result = 1;
    labelOnly.ply    = 999;
    labelOnly.move   = trajectory.samples.back().move;
    keyed            = atomic_v3_feature_input_key(labelOnly, relabeled);
    expect(bool(keyed) && first == relabeled, "feature.label-free",
           "score/result/ply/move changed the exact V3 model-input identity");
    keyed = atomic_v3_feature_input_key(trajectory.samples.back(), changed);
    expect(bool(keyed) && first != changed, "feature.position-sensitive",
           "different model inputs produced the same feature identity");

    const auto stamp =
      std::to_string(std::chrono::high_resolution_clock::now().time_since_epoch().count());
    const auto directory = std::filesystem::temp_directory_path() / ("atomic-v3-sort-" + stamp);
    std::filesystem::create_directory(directory);
    const auto raw      = directory / "keys.raw";
    const auto unique   = directory / "keys.unique";
    const auto rejected = directory / "keys.rejected";
    {
        std::ofstream output(raw, std::ios::binary);
        output.write(reinterpret_cast<const char*>(changed.data()), changed.size());
        output.write(reinterpret_cast<const char*>(first.data()), first.size());
        output.write(reinterpret_cast<const char*>(first.data()), first.size());
    }
    u64  uniqueRecords = 0;
    auto sorted        = sort_unique_atomic_v3_keys(raw, unique, 3, false, uniqueRecords);
    expect(bool(sorted) && uniqueRecords == 2, "feature.external-sort",
           sorted ? "external sort did not deduplicate exactly" : sorted.message);
    uniqueRecords  = 0;
    auto duplicate = sort_unique_atomic_v3_keys(raw, rejected, 3, true, uniqueRecords);
    expect(!duplicate && !std::filesystem::exists(rejected), "feature.duplicate-reject",
           "duplicate-rejecting external sort published an output");
    std::error_code ec;
    std::filesystem::remove_all(directory, ec);
}

void test_ordered_commit_wakeup() {
    std::atomic<u64>        nextCommitGameId{0};
    std::mutex              mutex;
    std::condition_variable pendingCv;
    std::condition_variable stateCv;
    constexpr usize         PendingLimit  = 4;
    const usize             pendingSize   = PendingLimit;
    bool                    waiterReady   = false;
    bool                    gameOneQueued = false;

    std::thread gameOneWorker([&]() {
        std::unique_lock<std::mutex> lock(mutex);
        waiterReady = true;
        stateCv.notify_one();
        pendingCv.wait(lock, [&]() {
            return pendingSize < PendingLimit
                || nextCommitGameId.load(std::memory_order_relaxed) == 1;
        });
        gameOneQueued = true;
        stateCv.notify_one();
    });

    {
        std::unique_lock<std::mutex> lock(mutex);
        stateCv.wait(lock, [&]() { return waiterReady; });
    }
    advance_atomic_v3_commit_game_id(nextCommitGameId, pendingCv, 1);

    bool woke = false;
    {
        std::unique_lock<std::mutex> lock(mutex);
        woke = stateCv.wait_for(lock, std::chrono::seconds(2), [&]() { return gameOneQueued; });
    }
    expect(woke, "scheduler.expected-id-wakeup",
           "game_id=1 remained asleep behind a full pending buffer");
    if (!woke)
    {
        nextCommitGameId.store(1, std::memory_order_relaxed);
        pendingCv.notify_all();
    }
    gameOneWorker.join();
}

void test_partition_and_ledger() {
    auto trajectory = make_trajectory("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                                      false, {"g1f3", "g8f6"});
    const auto groupA =
      atomic_v3_split_group_id(trajectory.rootPosition, false, trajectory.playedMoves);
    const auto groupB =
      atomic_v3_split_group_id(trajectory.rootPosition, false, trajectory.playedMoves);
    expect(groupA == groupB, "partition.deterministic-group", "group hash changed");
    expect(atomic_v3_partition_hash(20260716, groupA) == atomic_v3_partition_hash(20260716, groupB),
           "partition.deterministic-role", "partition hash changed");

    auto relabeled              = trajectory;
    relabeled.terminalResult    = 1;
    relabeled.samples[0].result = 1;
    relabeled.samples[1].result = -1;
    relabeled.stopReason        = AtomicV3StopReason::EVALUATION_RESIGNATION;
    expect(groupA == atomic_v3_split_group_id(relabeled.rootPosition, false, relabeled.playedMoves),
           "partition.label-free", "label changed split identity");

    const u64  seed      = 20260716;
    const u64  threshold = std::numeric_limits<u64>::max() / 2;
    const auto role      = atomic_v3_partition_role(seed, threshold, groupA);
    const auto stamp =
      std::to_string(std::chrono::high_resolution_clock::now().time_since_epoch().count());
    const auto directory = std::filesystem::temp_directory_path() / ("atomic-v3-ledger-" + stamp);
    std::filesystem::create_directory(directory);
    const auto ledgerPath = directory / "role.attraj";

    {
        AtomicV3TrajectoryLedgerStager ledger(directory, "role", role, seed, threshold,
                                              u32(trajectory.playedMoves.size()));
        auto                           appended = ledger.append(trajectory, 0);
        expect(bool(appended), "ledger.append", appended.message);

        AtomicV3LedgerMetadata metadata;
        auto                   finalized = ledger.finalize(
          ledgerPath, "0000000000000000000000000000000000000000000000000000000000000000", metadata);
        expect(bool(finalized), "ledger.finalize", finalized.message);
        expect(metadata.records == trajectory.samples.size() && metadata.trajectories == 1
                 && metadata.moves == trajectory.playedMoves.size()
                 && metadata.bytes == 160 + 112 + 4 * trajectory.playedMoves.size(),
               "ledger.metadata", "counts or fixed-size framing differ");
        bool stagingRemoved = true;
        for (const auto* suffix :
             {".entries.partial", ".moves.partial", ".groups.partial", ".groups.sorted.partial"})
            stagingRemoved &= !std::filesystem::exists(directory / (std::string("role") + suffix));
        expect(stagingRemoved, "ledger.finalized-staging-cleanup",
               "successful finalize leaked a private staging file");

        std::ifstream                  input(ledgerPath, std::ios::binary);
        std::array<unsigned char, 160> header{};
        input.read(reinterpret_cast<char*>(header.data()), header.size());
        expect(input.gcount() == std::streamsize(header.size())
                 && std::string(reinterpret_cast<char*>(header.data()), 7) == "ATTRAJ1"
                 && header[20] == static_cast<unsigned char>(role),
               "ledger.header", "magic or role differs");

        const auto wrongRole = role == AtomicV3DatasetRole::TRAIN ? AtomicV3DatasetRole::VALIDATION
                                                                  : AtomicV3DatasetRole::TRAIN;
        AtomicV3TrajectoryLedgerStager wrong(directory, "wrong", wrongRole, seed, threshold,
                                             u32(trajectory.playedMoves.size()));
        expect(!wrong.append(trajectory, 0), "ledger.wrong-role", "wrong partition accepted");

        const auto                     abortEntries = directory / "abort.entries.partial";
        AtomicV3TrajectoryLedgerStager rollback(directory, "abort", role, seed, threshold,
                                                u32(trajectory.playedMoves.size()));
        expect(bool(rollback.append(trajectory, 0)), "ledger.rollback-append", "append failed");
        expect(std::filesystem::exists(abortEntries), "ledger.rollback-created",
               "staging file not created");
        expect(bool(rollback.abort()) && !std::filesystem::exists(abortEntries), "ledger.rollback",
               "abort left a staged file");

        const auto                     finalizedAbortPath = directory / "finalized-abort.attraj";
        AtomicV3TrajectoryLedgerStager finalizedRollback(
          directory, "finalized-abort", role, seed, threshold, u32(trajectory.playedMoves.size()));
        expect(bool(finalizedRollback.append(trajectory, 0)), "ledger.finalized-abort-append",
               "append before explicit finalized rollback failed");
        AtomicV3LedgerMetadata finalizedAbortMetadata;
        expect(
          bool(finalizedRollback.finalize(
            finalizedAbortPath, "0000000000000000000000000000000000000000000000000000000000000000",
            finalizedAbortMetadata))
            && std::filesystem::exists(finalizedAbortPath),
          "ledger.finalized-abort-finalize", "finalized rollback fixture was not published");
        expect(bool(finalizedRollback.abort()) && !std::filesystem::exists(finalizedAbortPath),
               "ledger.finalized-explicit-abort",
               "explicit abort did not remove a finalized rollback output");

        const auto                     duplicatePath = directory / "duplicate.attraj";
        AtomicV3TrajectoryLedgerStager duplicateLedger(
          directory, "duplicate", role, seed, threshold, u32(trajectory.playedMoves.size()));
        expect(bool(duplicateLedger.append(trajectory, 0)), "ledger.duplicate-first",
               "first duplicate fixture append failed");
        expect(bool(duplicateLedger.append(trajectory, trajectory.samples.size())),
               "ledger.duplicate-second", "second duplicate fixture append failed");
        AtomicV3LedgerMetadata duplicateMetadata;
        auto                   duplicateFinalized = duplicateLedger.finalize(
          duplicatePath, "0000000000000000000000000000000000000000000000000000000000000000",
          duplicateMetadata);
        expect(!duplicateFinalized && !std::filesystem::exists(duplicatePath),
               "ledger.duplicate-group", "duplicate split_group_id was published");
    }

    expect(std::filesystem::exists(ledgerPath), "ledger.finalized-survives-destruction",
           "destructor removed a successfully finalized trajectory ledger");

    std::error_code ec;
    std::filesystem::remove_all(directory, ec);
}

}  // namespace

int main() {
    Bitboards::init();
    Attacks::init();
    Position::init();
    test_semantic_fixtures();
    test_mutations();
    test_feature_identity_and_external_sort();
    test_ordered_commit_wakeup();
    test_partition_and_ledger();
    if (Failures)
        return 1;
    std::cout << "Atomic V3 trajectory tests passed\n";
    return 0;
}
