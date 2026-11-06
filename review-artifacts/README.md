# ARTREVIEW evidence

Reviewed source: `3131ad4dfab828d9ccbaec87236f7b56d1a91066`.
Report: `/home/wilf/data/PodJS/ipod/ARTREVIEW-REPORT.md`.

Run from the worktree root:

```sh
python3 review-artifacts/run_checks.py
python3 review-artifacts/run_decode_checks.py
```

`run_checks.py` extracts selected original C functions and inserts their original
file and line numbers. It supplies small structs and hardware/I/O stubs. Its
playback tests stub image loading, its resolver tests use a case-insensitive
virtual file set, and its cache decisions stub file existence. Its `.aat` reader
uses real fixture files. Thus it proves decisions in the functions, not full
firmware integration. Two intentionally failing subprocesses show missing-tag
invalid reads under ASan/UBSan. Their failure is expected by the runner.

`run_decode_checks.py` compiles unmodified image_load, JPEG, PNG, GIF, WebP, BMP,
resizer, byte-stream and inflate sources with ASan/UBSan. It links original
metadata stream helpers without sanitizers and a local read-only POSIX adapter.
It uses the ipod6g simulator build configuration, including native RGB565. It
does not run the simulator UI or any device. Dependencies are host GCC, make,
SDL2 development files and Pillow. Fixture outputs are PPM files; pixel probes
are in `pixel-checks.json`. No external image download is used.

The chosen paths in `hash-collision.json` also pass through the original C hash
in `cache_probe.log`. They are virtual path strings, not created music folders.
`itunes-reference.json` contains names, sizes and only the first 32 bytes of the
four read-only backup files. It does not establish a track-to-artwork mapping.

`function-run.log` and `decode-run.log` are runner transcripts.
`*-probe*.log`/`decode-results.log` contain case-level output.
`source-sha256.txt` pins the principal audited source files.

No test here covers physical-device timing, preemptive interleavings, stack
high-water marks, power loss, USB handover or a full user-visible UI session.
The product source is unchanged; all local additions belong to this review.
