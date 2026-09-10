# rolling-hash-delta-sync
An rsync-style binary delta sync tool built from scratch: rolling Adler-32-style weak checksum + blake2b strong hash to compute and apply minimal deltas between file versions. CLI: deltasync.
