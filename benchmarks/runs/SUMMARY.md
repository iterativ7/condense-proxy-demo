Benchmark run outputs

Folders here are gitignored except this file and .gitkeep.

After a matrix run you will have:

  benchmarks/runs/profile-matrix/SUMMARY.md
  benchmarks/runs/profile-matrix/<profile>__<mode>/REPORT.md
  benchmarks/runs/profile-matrix/<profile>__<mode>/report.json
  benchmarks/runs/profile-matrix/<profile>__<mode>/results.jsonl

SUMMARY.md is created by:

  python benchmarks/summarize_profile_matrix.py

Full instructions: benchmarks/README.md Step 8.
