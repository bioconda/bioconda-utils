"""Strict Zstd decoding shared by full and sharded repodata."""

import zstandard


def decompress_zstd(data: bytes) -> bytes:
    """Decode complete frames, including frames without declared output sizes.

    Require EOF for every frame: truncated input can yield a complete JSON or
    MessagePack payload before its checksum has been received and validated.
    """
    chunks = []
    while data:
        decoder = zstandard.ZstdDecompressor().decompressobj()
        chunks.append(decoder.decompress(data))
        if not decoder.eof:
            raise zstandard.ZstdError("Incomplete zstd frame")
        data = decoder.unused_data
    if not chunks:
        raise zstandard.ZstdError("Empty zstd response")
    return b"".join(chunks)
