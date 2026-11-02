# ci/profile-1102-attr

Re-runs the attribution of the AtomicDB worker profile (ci/profile-1102, run
41388418383) with a fixed `ci/perf_attr.py`: the samples recorded without call
graphs are now read (perf right-aligns the comm field), and conversion operators
such as `StatsEntry::operator short()` are filed under history tables. The
workflow downloads that run's perf scripts and binaries and symbolizes them again
with llvm-symbolizer 23. Nothing is recorded again; nothing is published.
